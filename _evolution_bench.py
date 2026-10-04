"""COSMOS Evolution benchmark: run the crew-scheduling battery for a code
change and compare the resulting metrics against a baseline.

The Evolution pipeline checks a candidate change out into its own repo
directory and invokes

    <candidate-repo>/.venv/Scripts/python.exe _evolution_bench.py \
        run --profile quick --out metrics.json

Because the orchestrator invokes this script WITH THE TARGET REPO'S OWN
venv python, ``sys.executable`` already is the right interpreter for the
stage subprocesses.  The command builders nevertheless prefer the target
repo's venv python when it exists (exactly like webapp/jobs.py does), and
fall back to ``sys.executable`` — the two are the same interpreter under
the orchestrator contract.

Command lines, environment and gate semantics are MIRRORED from
webapp/jobs.py (the web app's job runner), so a bench run is the very same
battery the app runs — only the budget values differ per profile:

  stage1 (column generation, GERAD instance N):
      _gerad_colgen.py N --bases BASE1,BASE2,BASE3 --pricer duty
          --max-landings 5 --mp 10 --price-cap 600 --final-tl 900
          --dump-selected --dump-pool --cert-budget 60
          --total-cap <budget_min * 60>
  stage2 (rostering):
      _gerad_stage2_roster.py N N <budget_s> <seed>

  env: inherited environment plus CREW_RULES_CONFIG ->
  <repo>/webapp/config/airline_default.json when that file exists (the
  Python engines/validators and the Kotlin GERAD profiles read it), and
  STAGE2_PORT allocated from the 9092-9098 pool by bind probe.

  Gate semantics (webapp/jobs.py contract): a nonzero stage-2 exit is NOT
  a crash when the run (re)wrote the assignment artifact —
  _gerad_stage2_roster.py returns the number of non-OK instances by
  design.  Such runs are collected normally, with "gate_nonzero": true.

Metrics come from the same artifacts webapp/results.py reads:

  stage1: final-report/best/meta_<label>.json and
          final-report/gerad_colgen_selected_<label>.json for
          ip_objective / covered (results.py _stage1 extraction), plus
          final-report/gerad_stage1_results.json (initial_budget_result)
          for lp_bound / lp_bound_valid / gap_vs_lp_pct.  wall_s is the
          stage-1 subprocess wall time measured by this script: the
          canonical artifacts are refreshed only on PROMOTION, so their
          own timing can belong to an older, better run.  The
          "promoted_this_run" flag records whether this run refreshed the
          canonical best (when false, the quality metrics echo the durable
          best rather than this run's result — read the compare
          accordingly).
  stage2: the last matching row of
          final-report/gerad_phase2_two_stage.json (results.py _stage2).

A nonzero exit of a stage records {"error": "..."} for that instance and
the battery CONTINUES with the next instance — partial metrics are still
useful.  ``compare`` never exits nonzero on regressions: the human decides.

Stdlib only — no new dependencies.
"""
from __future__ import annotations

import _data_paths
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

from _cli_console import force_utf8_console  # noqa: E402

force_utf8_console()

SCHEMA = "evolution-bench/v1"

# Battery profiles: quick for cheap iteration inside the Evolution loop,
# full for the accepted-battery budgets (webapp defaults: 75 min / 600 s).
PROFILES = {
    "quick": {"instances": (1, 2, 3, 4), "stage1_budget_min": 10,
              "stage2_budget_s": 300, "seed": 7},
    "full": {"instances": (1, 2, 3, 4, 5, 6, 7), "stage1_budget_min": 75,
             "stage2_budget_s": 600, "seed": 7},
}

# Mirror of webapp/jobs.py STAGE2_PORT_POOL (stage-2 isolation ports).
STAGE2_PORT_POOL = list(range(9092, 9099))

# ------------------------------------------------------------- metric catalog
# Collected per stage, in display order.
STAGE1_METRICS = ("ip_objective", "lp_bound", "lp_bound_valid",
                  "gap_vs_lp_pct", "covered", "wall_s")
STAGE2_METRICS = ("result_status", "stage2_score", "unassigned",
                  "vacation_violations", "pref_hits",
                  "trajectory_violations", "missing_legs", "wall_s")

# Тайминги показываются с полярностью, но НЕ входят в вердикт/гейт:
# wall-clock недетерминирован (ревью пакета: worse==0 по wall_s делал Stage
# практически непроходимым — любой шум в 0.1 c валил кандидата).
TIMING_METRICS = {"wall_s"}
LOWER_BETTER = {"ip_objective", "gap_vs_lp_pct", "unassigned",
                "vacation_violations", "trajectory_violations",
                "missing_legs", "wall_s"}
HIGHER_BETTER = {"covered", "pref_hits"}
# No agreed polarity: shown when changed, never judged better/worse.
# (lp_bound moves with the relaxation, stage2_score is a hard/soft string.)
INFO_ONLY = {"lp_bound", "stage2_score"}

# Мягкие метрики этапа 2, НЕДЕТЕРМИНИРОВАННЫЕ по зерну и по времени
# останова солвера: на ОДНОМ зерне они гуляют так, что сравнение двух
# каталогов по одной точке ничего не значит.
#
# Проверка находки 27 (2026-07-24): vacation_violations на inst4 на ОДНОМ
# И ТОМ ЖЕ каталоге дал 331..419 (размах 88) только от смены зерна, а на
# одном зерне воспроизводился до ±19 — этап 2 останавливается по времени,
# а не по сходимости. Разница кандидат-vs-база в +25 тонула в этом шуме, и
# жёсткое «любое ухудшение = regressed» ложно забраковало годную находку
# (её ПОКРЫТИЕ при этом улучшалось на КАЖДОМ зерне). Поэтому мягкие
# метрики показываются с полярностью, но в вердикт НЕ входят — ровно как
# wall_s. Гейтить их по-настоящему можно только по нескольким зёрнам
# (медиана); бенч однозёренный (seed=7), поэтому — совещательно.
#
# ЖЁСТКИЕ/покрытийные метрики этапа 2 (unassigned, missing_legs,
# trajectory_violations, result_status) в вердикте ОСТАЮТСЯ: они про
# легальность и покрытие, а их ухудшение — настоящий регресс.
STOCHASTIC_METRICS = {"vacation_violations", "pref_hits"}
# Всё, что вне жёсткого вердикта: тайминги + мягкие стохастические.
ADVISORY_METRICS = TIMING_METRICS | STOCHASTIC_METRICS


# ----------------------------------------------------------- command building

def repo_python(repo: Path) -> str:
    """Interpreter for the stage subprocesses: the target repo's own venv
    python (same layout probe as webapp/jobs.py — Scripts/ on Windows,
    bin/ elsewhere), falling back to sys.executable, which the
    orchestrator contract already makes the target repo's venv python."""
    repo = Path(repo)
    for cand in (repo / ".venv" / "Scripts" / "python.exe",
                 repo / ".venv" / "bin" / "python"):
        if cand.exists():
            return str(cand)
    return sys.executable


def build_stage1_cmd(inst_n: int, budget_min: int, repo: Path,
                     python: str | None = None) -> list[str]:
    """Stage-1 command line — exact mirror of webapp/jobs.py
    _build_commands("stage1", <gerad instance>): corrected-space duty run,
    budget_min caps the column-generation loop via --total-cap."""
    if budget_min <= 0:
        raise ValueError("budget_min must be positive")
    py = python if python is not None else repo_python(repo)
    return [py, "_gerad_colgen.py", str(int(inst_n)),
            "--bases", "BASE1,BASE2,BASE3",
            "--pricer", "duty", "--max-landings", "5",
            "--mp", "10", "--price-cap", "600", "--final-tl", "900",
            "--dump-selected", "--dump-pool",
            "--cert-budget", "60",
            "--total-cap", str(int(budget_min) * 60)]


def build_stage2_cmd(inst_n: int, budget_s: int, seed: int, repo: Path,
                     python: str | None = None) -> list[str]:
    """Stage-2 command line — exact mirror of webapp/jobs.py
    _build_commands("stage2", <gerad instance>): numeric GERAD mode,
    positional lo hi budget seed."""
    if budget_s <= 0:
        raise ValueError("budget_s must be positive")
    py = python if python is not None else repo_python(repo)
    n = str(int(inst_n))
    return [py, "_gerad_stage2_roster.py", n, n,
            str(int(budget_s)), str(int(seed))]


def build_env(repo: Path) -> dict:
    """Mirror of webapp/jobs.py _base_env(): the inherited environment plus
    CREW_RULES_CONFIG -> <repo>/webapp/config/airline_default.json when
    that file exists — this is what makes the saved airline rule config
    actually applied by the engines."""
    env = os.environ.copy()
    cfg = Path(repo) / "webapp" / "config" / "airline_default.json"
    if cfg.exists():
        env["CREW_RULES_CONFIG"] = str(cfg.resolve())
    # Unbuffered solver stdout so per-iteration progress lines reach the
    # tee (and hence the live feed) as they happen, not in block chunks.
    env["PYTHONUNBUFFERED"] = "1"
    return env


def allocate_stage2_port() -> int:
    """First actually-free port of the webapp's stage-2 pool (bind probe,
    like webapp/jobs.py — long-lived JVMs from other experiments may squat
    on the pool).  The bench runs stages sequentially, so no in-use
    bookkeeping is needed.  Loud failure when the pool is exhausted."""
    for port in STAGE2_PORT_POOL:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError("no free STAGE2 port in pool 9092-9098")


# --------------------------------------------------------- metric extraction

def _load_json_or_none(path: Path):
    """Parsed artifact or None when absent/unreadable — mirrors the
    tolerance of webapp/registry._load_json_cached: results are reported
    from whatever artifacts exist, a missing artifact is not a crash."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def extract_stage1_metrics(meta, selected, record, wall_s) -> dict:
    """Stage-1 metrics from parsed artifacts.

    meta:     final-report/best/meta_<label>.json
    selected: final-report/gerad_colgen_selected_<label>.json
    record:   the <label> entry of final-report/gerad_stage1_results.json

    ip_objective and covered replicate webapp/results.py _stage1 exactly
    (meta wins over the catalog dump; covered is the union of active_fns,
    falling back to fns per pairing).  lp_bound / lp_bound_valid /
    gap_vs_lp_pct come from the canonical record's initial_budget_result.
    """
    if meta is None and selected is None and record is None:
        return {"error": "no stage1 artifacts (best/meta, selected catalog, "
                         "gerad_stage1_results.json)"}
    meta = meta if isinstance(meta, dict) else {}
    selected = selected if isinstance(selected, dict) else {}
    covered = None
    if isinstance(selected.get("selected"), list):
        legs: set = set()
        for rec in selected["selected"]:
            legs.update(rec.get("active_fns", rec.get("fns", [])))
        covered = len(legs)
    ibr = {}
    if isinstance(record, dict) and \
            isinstance(record.get("initial_budget_result"), dict):
        ibr = record["initial_budget_result"]
    return {
        "ip_objective": meta.get("ip_objective", selected.get("ip_objective")),
        "lp_bound": ibr.get("lp_bound"),
        "lp_bound_valid": ibr.get("lp_bound_valid"),
        "gap_vs_lp_pct": ibr.get("gap_vs_lp_pct"),
        "covered": covered,
        "wall_s": wall_s,
    }


def extract_stage2_metrics(journal, inst_n: int) -> dict | None:
    """Stage-2 metrics from the two-stage journal
    (final-report/gerad_phase2_two_stage.json).  Replicates
    webapp/results.py _stage2: the journal appends, the LAST row matching
    the instance wins.  None when the instance has no row."""
    row = None
    if isinstance(journal, list):
        for r in journal:
            if isinstance(r, dict) and r.get("inst") == inst_n:
                row = r
    if row is None:
        return None
    return {
        "result_status": row.get("result_status"),
        "stage2_score": row.get("stage2_score"),
        "unassigned": row.get("unassigned"),
        "vacation_violations": row.get("vacation_violations"),
        "pref_hits": row.get("pref_hits"),
        "trajectory_violations": row.get("trajectory_violations"),
        "missing_legs": row.get("missing_legs"),
        "wall_s": row.get("wall_s"),
    }


def _collect_stage1(repo: Path, label: str, wall_s: float,
                    started_ts: float) -> dict:
    fr = _data_paths.results_dir()
    meta_path = fr / "best" / f"meta_{label}.json"
    meta = _load_json_or_none(meta_path)
    selected = _load_json_or_none(fr / f"gerad_colgen_selected_{label}.json")
    results = _load_json_or_none(fr / "gerad_stage1_results.json")
    record = results.get(label) if isinstance(results, dict) else None
    metrics = extract_stage1_metrics(meta, selected, record, wall_s)
    if "error" not in metrics:
        # The canonical artifacts are refreshed only on PROMOTION (a weak
        # run keeps its record under runs/stage1/ and the durable best
        # stands) — flag whether THIS run refreshed them, so a compare of
        # stale canonical numbers is readable as such.
        try:
            metrics["promoted_this_run"] = \
                meta_path.stat().st_mtime >= started_ts - 1.0
        except OSError:
            metrics["promoted_this_run"] = False
    return metrics


def _collect_stage2(repo: Path, inst_n: int) -> dict | None:
    journal = _load_json_or_none(
        _data_paths.results_dir() / "gerad_phase2_two_stage.json")
    return extract_stage2_metrics(journal, inst_n)


# ------------------------------------------------------------------ run

def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _write_json(path: Path, data) -> None:
    """Atomic-ish JSON write (tmp + replace), UTF-8.

    On Windows the final rename can transiently fail with PermissionError
    (WinError 5) when the destination is momentarily held open by another
    process — antivirus, the file indexer, or a status poller stat-ing the
    output. That is not a real failure: retry the rename a few times with a
    short backoff before giving up (and only then re-raise, so a genuinely
    stuck destination still fails loudly)."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    last_exc: PermissionError | None = None
    for attempt in range(10):
        try:
            tmp.replace(path)
            return
        except PermissionError as exc:      # Windows: dest transiently locked
            last_exc = exc
            time.sleep(0.3 * (attempt + 1))
    raise last_exc


def git_head(repo: Path) -> str | None:
    """HEAD commit of the target repo; None (with a stderr warning) when
    git is unavailable — a bench run without provenance is still useful."""
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo),
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"warning: git rev-parse HEAD failed in {repo}: {exc}",
              file=sys.stderr)
        return None
    if proc.returncode != 0:
        print(f"warning: git rev-parse HEAD failed in {repo}: "
              f"{proc.stderr.strip()}", file=sys.stderr)
        return None
    return proc.stdout.strip()


# Solver stdout lines worth surfacing as live within-instance progress
# (echoed to the bench's own stdout as "[instN] progress: ..." so the
# evolution runner's activity feed can show them). Stage-1 prints a line
# per column-generation iteration; the milestone lines below bracket both
# stages. Everything else still goes to the per-instance log only.
_PROG_RE = re.compile(
    r"^\s*(it\s+\d+:|CG done|CP-SAT:|"
    r"stage2=|pref_hits=|Construction Heuristic|Local Search phase)",
    re.IGNORECASE)


def _run_logged(cmd: list[str], repo: Path, env: dict, log_path: Path,
                append: bool, label: str | None = None) -> int:
    """Run one stage subprocess exactly like webapp/jobs.py _watch():
    cwd = repo root, stdout+stderr merged and streamed into the log file,
    an '=== run:' header per command.  Returns the exit code.

    Unlike a raw OS-level ``stdout=fh`` redirect, this drains the pipe in
    Python so it can TEE: every line still goes to the log, and lines that
    look like solver progress are ALSO echoed to this process's stdout as
    ``[<label>] progress: <line>`` for the live feed (a single OS pipe
    cannot fork to both a file and stdout). Progress echo is best-effort;
    the log remains the source of truth."""
    with open(log_path, "ab" if append else "wb") as fh:
        fh.write(("=== run: " + " ".join(cmd) + "\n").encode("utf-8"))
        fh.flush()
        proc = subprocess.Popen(
            cmd, cwd=str(repo), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, env=env, bufsize=1,
            text=True, encoding="utf-8", errors="replace")
        for line in proc.stdout:
            fh.write(line.encode("utf-8", "replace"))
            fh.flush()
            if label and _PROG_RE.search(line):
                print(f"[{label}] progress: {line.rstrip()}", flush=True)
        return proc.wait()


def _stage2_artifact_completed(repo: Path, label: str,
                               started_ts: float) -> bool:
    """Mirror of webapp/jobs.py _stage2_artifact_completed: true when this
    run (re)wrote the assignment artifact — the signal that a nonzero exit
    is a gate verdict, not a crash.

    P0-1 (аудит документации 2026-07-20): не-OK прогон больше не переписывает
    канонический артефакт — свежий per-run манифест метки равносилен
    завершённому гейту (bench не выставляет CREW_RUN_ID — mtime-фолбэк),
    иначе кандидат с не-OK вердиктом ложно считался бы крахом."""
    mdir = _data_paths.results_dir() / "runs" / "roster-manifests" / label
    try:
        for p in mdir.glob("*.json"):
            if p.stat().st_mtime >= started_ts - 1.0:
                return True
    except OSError:
        pass
    path = _data_paths.results_dir() / f"gerad_stage2_assignment_{label}.json"
    try:
        return path.stat().st_mtime >= started_ts - 1.0
    except OSError:
        return False


def run_battery(profile_name: str, out_path: Path, repo: Path) -> dict:
    """Run stage1+stage2 for every profile instance inside `repo`, stream
    each instance's output to bench_<label>.log next to `out_path`, and
    write the metrics document after every instance (partial results
    survive a later crash)."""
    profile = PROFILES[profile_name]          # argparse guarantees the key
    repo = Path(repo).resolve()
    out_path = Path(out_path).resolve()
    if not (repo / "_gerad_colgen.py").exists():
        raise SystemExit(f"{repo} does not look like a crew-demo checkout "
                         f"(_gerad_colgen.py missing)")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    env = build_env(repo)
    result = {
        "schema": SCHEMA,
        "profile": profile_name,
        "commit": git_head(repo),
        "started_at": _now(),
        "finished_at": None,
        "instances": {},
    }
    for n in profile["instances"]:
        label = f"inst{n}"
        log = out_path.parent / f"bench_{label}.log"
        entry: dict = {}
        print(f"[{label}] stage1: budget {profile['stage1_budget_min']} min "
              f"-> {log.name}", flush=True)
        t0 = time.time()
        rc1 = _run_logged(
            build_stage1_cmd(n, profile["stage1_budget_min"], repo),
            repo, env, log, append=False, label=label)
        wall1 = round(time.time() - t0, 1)
        if rc1 != 0:
            entry["stage1"] = {"error": f"stage1 exit code {rc1} "
                                        f"(see {log.name})"}
            # A failed stage-1 leaves no fresh catalog; rostering over a
            # stale one would benchmark the wrong thing.
            entry["stage2"] = {"error": "skipped: stage1 failed"}
        else:
            entry["stage1"] = _collect_stage1(repo, label, wall1, t0)
            env2 = dict(env)
            env2["STAGE2_PORT"] = str(allocate_stage2_port())
            print(f"[{label}] stage2: budget {profile['stage2_budget_s']} s, "
                  f"seed {profile['seed']}, port {env2['STAGE2_PORT']}",
                  flush=True)
            t1 = time.time()
            rc2 = _run_logged(
                build_stage2_cmd(n, profile["stage2_budget_s"],
                                 profile["seed"], repo),
                repo, env2, log, append=True, label=label)
            if rc2 == 0 or (rc2 > 0
                            and _stage2_artifact_completed(repo, label, t1)):
                metrics = _collect_stage2(repo, n)
                if metrics is None:
                    entry["stage2"] = {
                        "error": f"stage2 finished (exit {rc2}) but the "
                                 f"journal has no row for inst {n}"}
                else:
                    if rc2 != 0:
                        # Gate semantics: verdict, not crash.
                        metrics["gate_nonzero"] = True
                    entry["stage2"] = metrics
            else:
                entry["stage2"] = {
                    "error": f"stage2 exit code {rc2} without a refreshed "
                             f"assignment artifact (see {log.name})"}
        result["instances"][label] = entry
        _write_json(out_path, result)
        s1 = "error" if "error" in entry["stage1"] else "ok"
        s2 = ("error" if "error" in entry["stage2"]
              else str(entry["stage2"].get("result_status")))
        print(f"[{label}] done: stage1={s1} stage2={s2}", flush=True)
    result["finished_at"] = _now()
    _write_json(out_path, result)
    print(f"metrics -> {out_path}", flush=True)
    return result


# ------------------------------------------------------------------ compare

def load_metrics_file(path) -> dict:
    """Parse and validate a metrics document.  Malformed input fails loud."""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read metrics file {p}: {exc}")
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        got = data.get("schema") if isinstance(data, dict) else type(data).__name__
        raise SystemExit(f"{p}: not an {SCHEMA} metrics file (schema={got!r})")
    if not isinstance(data.get("instances"), dict):
        raise SystemExit(f"{p}: 'instances' must be a JSON object")
    return data


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _classify(metric: str, base, cand) -> str:
    """better / worse / equal with the metric's polarity; n/a when either
    side is missing; info for metrics without an agreed polarity.  A
    present-but-non-numeric value for a numeric metric is malformed input
    and fails loud."""
    if base is None or cand is None:
        return "n/a"
    if metric == "lp_bound_valid":            # true > false
        b, c = bool(base), bool(cand)
        return "equal" if b == c else ("better" if c else "worse")
    if metric == "result_status":             # OK best
        b, c = base == "OK", cand == "OK"
        return "equal" if b == c else ("better" if c else "worse")
    if metric in INFO_ONLY:
        return "info"
    for v, side in ((base, "baseline"), (cand, "candidate")):
        if not _is_number(v):
            raise ValueError(f"metric {metric}: {side} value {v!r} "
                             f"is not a number")
    if cand == base:
        return "equal"
    if metric in LOWER_BETTER:
        return "better" if cand < base else "worse"
    if metric in HIGHER_BETTER:
        return "better" if cand > base else "worse"
    raise ValueError(f"metric {metric} has no polarity defined")


def _fmt(v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _fmt_delta(base, cand) -> str:
    """Signed delta with a percentage for numeric pairs; '-' otherwise."""
    if _is_number(base) and _is_number(cand):
        d = cand - base
        pct = f" ({100.0 * d / base:+.1f}%)" if base else ""
        return f"{d:+g}{pct}"
    return "-"


def _fmt_change(base, cand) -> str:
    """Highlight form: '58787 -> 58121 (-1.1%)'."""
    if _is_number(base) and _is_number(cand) and base:
        return (f"{_fmt(base)} -> {_fmt(cand)} "
                f"({100.0 * (cand - base) / base:+.1f}%)")
    return f"{_fmt(base)} -> {_fmt(cand)}"


def _inst_sort_key(name: str):
    m = re.fullmatch(r"inst(\d+)", name)
    return (0, int(m.group(1)), "") if m else (1, 0, name)


def _highlight_key(row: dict):
    """Status flips first, quality numerics by |pct| next, wall time last."""
    metric = row["metric"]
    prio = (0 if metric in ("result_status", "lp_bound_valid")
            else 2 if metric == "wall_s" else 1)
    b, c = row["base"], row["cand"]
    mag = 0.0
    if _is_number(b) and _is_number(c):
        mag = abs(100.0 * (c - b) / b) if b else abs(c - b)
    return (prio, -mag, row["inst"], row["stage"], metric)


MAX_TABLE_ROWS = 24
MAX_NOTES = 5
MAX_HIGHLIGHTS = 5


def compare_data(baseline: dict, candidate: dict) -> tuple[str, dict]:
    """(markdown, machine summary) for two metrics documents.

    Missing instances/stages/metrics on either side compare as n/a, never
    crash.  The markdown is Telegram-compact: only changed-or-notable
    metrics are listed, unchanged ones are summarized as a count."""
    rows: list[dict] = []     # changed / notable per-metric records
    notes: list[str] = []     # stage-level errors and missing sides
    counts = {"better": 0, "worse": 0, "equal": 0, "n/a": 0,
              "timing": 0, "soft": 0}
    # H16: stages where the BASELINE succeeded but the CANDIDATE errored.
    # This is a fatal regression (a broken solver), NOT a neutral n/a — the
    # promotion gate must refuse it regardless of any other improved metric.
    candidate_errors = 0
    metric_holes = 0

    insts = sorted(set(baseline["instances"]) | set(candidate["instances"]),
                   key=_inst_sort_key)
    for inst in insts:
        b_inst = baseline["instances"].get(inst) or {}
        c_inst = candidate["instances"].get(inst) or {}
        if not isinstance(b_inst, dict) or not isinstance(c_inst, dict):
            raise SystemExit(f"instance {inst}: entry must be a JSON object")
        for stage, metric_names in (("stage1", STAGE1_METRICS),
                                    ("stage2", STAGE2_METRICS)):
            b_stage = b_inst.get(stage)
            c_stage = c_inst.get(stage)
            b_ok = isinstance(b_stage, dict) and "error" not in b_stage
            c_ok = isinstance(c_stage, dict) and "error" not in c_stage
            for ok, d, side in ((b_ok, b_stage, "baseline"),
                                (c_ok, c_stage, "candidate")):
                if ok:
                    continue
                if isinstance(d, dict) and "error" in d:
                    notes.append(f"{inst} {stage} {side} error: "
                                 f"{str(d['error'])[:100]}")
                else:
                    notes.append(f"{inst} {stage}: missing on {side} side")
            if b_ok and not c_ok:
                candidate_errors += 1
            if not b_ok and not c_ok:
                counts["n/a"] += len(metric_names)
                continue
            bd = b_stage if b_ok else {}
            cd = c_stage if c_ok else {}
            for metric in metric_names:
                b, c = bd.get(metric), cd.get(metric)
                if b is None and c is None:
                    counts["n/a"] += 1
                    continue
                # Аудит-2 P1-10: метрика, которую база отдала, а кандидат —
                # нет, это ДЫРА схемы метрик, а не нейтральное n/a: пустой
                # кандидатский прогон проходил Stage как no_gain.
                if b_ok and c_ok and b is not None and c is None:
                    metric_holes += 1
                cls = _classify(metric, b, c)
                row = {"inst": inst, "stage": stage, "metric": metric,
                       "base": b, "cand": c, "cls": cls}
                if metric in ADVISORY_METRICS and cls in ("better", "worse"):
                    # вне жёсткого вердикта: строку показываем с полярностью,
                    # но считаем в совещательном бакете — тайминги отдельно
                    # от мягких стохастических, чтобы отчёт был честен
                    counts["timing" if metric in TIMING_METRICS
                           else "soft"] += 1
                    rows.append(row)
                    continue
                if cls == "n/a":
                    counts["n/a"] += 1
                    if b_ok and c_ok:      # partial hole, worth showing
                        rows.append(row)
                elif cls == "info":
                    if b == c:
                        counts["equal"] += 1
                    else:
                        row["cls"] = "~"   # changed, no polarity judgement
                        rows.append(row)
                else:
                    counts[cls] += 1
                    if cls != "equal":
                        rows.append(row)

    judged = [r for r in rows if r["cls"] in ("better", "worse")
              and r["metric"] not in ADVISORY_METRICS]
    highlights = [f"{r['inst']} {r['stage']} {r['metric']} "
                  f"{_fmt_change(r['base'], r['cand'])}"
                  for r in sorted(judged, key=_highlight_key)
                  ][:MAX_HIGHLIGHTS]
    # A candidate that crashed where the baseline ran is a regression, not an
    # improvement — never let a stray better-metric label it "improved" (H16).
    # Аудит-2 P1-10: любое ухудшение или дыра в схеме метрик — тоже
    # регрессия: «improved при 4 worse + 1 better» больше не существует.
    if candidate_errors > 0 or counts["worse"] > 0 or metric_holes > 0:
        verdict = "regressed"
    elif counts["better"] >= 1:
        verdict = "improved"
    else:
        verdict = "no_gain"
    summary = {"verdict": verdict, "better": counts["better"],
               "worse": counts["worse"], "equal": counts["equal"],
               "timing_changes": counts["timing"],
               "soft_changes": counts["soft"],
               "candidate_errors": candidate_errors,
               "metric_holes": metric_holes,
               "highlights": highlights}

    def _side(doc):
        commit = doc.get("commit") or "?"
        return f"commit {str(commit)[:12]}, profile {doc.get('profile')}"

    lines = ["# Evolution bench: baseline vs candidate",
             f"- baseline: {_side(baseline)}",
             f"- candidate: {_side(candidate)}"]
    if baseline.get("profile") != candidate.get("profile"):
        lines.append("- WARNING: profiles differ — numbers are not "
                     "directly comparable")
    _verdict_word = {"improved": "IMPROVED", "no_gain": "NO GAIN",
                     "regressed": "REGRESSED"}.get(verdict, verdict.upper())
    lines.append(f"- verdict: **{_verdict_word}** "
                 f"(better {counts['better']}, worse {counts['worse']}, "
                 f"equal {counts['equal']}, n/a {counts['n/a']})")
    if counts["worse"] >= 1:
        lines.append(f"- **REGRESSIONS PRESENT** ({counts['worse']} worse)")
    if metric_holes >= 1:
        lines.append(f"- **METRIC SCHEMA HOLES** ({metric_holes} метрик есть "
                     f"у базы, но нет у кандидата)")
    if counts["soft"] >= 1:
        lines.append(f"- ADVISORY: {counts['soft']} мягк. метрик(и) этапа 2 "
                     f"изменились, но в вердикт НЕ входят (шум одного зерна: "
                     f"vacation_violations гуляет ~90 на inst4 только от "
                     f"зерна — см. STOCHASTIC_METRICS)")
    if highlights:
        lines += ["", "## Highlights"]
        lines += [f"- {h}" for h in highlights]
    if rows:
        lines += ["", "## Changed metrics",
                  "| instance | stage | metric | baseline | candidate "
                  "| delta | class |",
                  "|---|---|---|---|---|---|---|"]
        for r in rows[:MAX_TABLE_ROWS]:
            lines.append(f"| {r['inst']} | {r['stage']} | {r['metric']} "
                         f"| {_fmt(r['base'])} | {_fmt(r['cand'])} "
                         f"| {_fmt_delta(r['base'], r['cand'])} "
                         f"| {r['cls']} |")
        if len(rows) > MAX_TABLE_ROWS:
            lines.append(f"- ... and {len(rows) - MAX_TABLE_ROWS} more "
                         f"changed metrics (see the metrics files)")
    else:
        lines += ["", "No metric changes."]
    if notes:
        lines += ["", "## Notes"]
        lines += [f"- {n}" for n in notes[:MAX_NOTES]]
        if len(notes) > MAX_NOTES:
            lines.append(f"- ... and {len(notes) - MAX_NOTES} more notes")
    lines.append("")
    lines.append(f"Unchanged metrics: {counts['equal']}; "
                 f"not comparable (n/a): {counts['n/a']}.")
    return "\n".join(lines) + "\n", summary


# ---------------------------------------------------------------------- CLI

def _cmd_run(args) -> int:
    # Default: the checkout this script itself lives in — the orchestrator
    # copies the script with the repo, so bench and code stay in lockstep.
    repo = Path(args.repo) if args.repo else Path(__file__).resolve().parent
    run_battery(args.profile, Path(args.out), repo)
    return 0


def _cmd_compare(args) -> int:
    baseline = load_metrics_file(args.baseline)
    candidate = load_metrics_file(args.candidate)
    md, summary = compare_data(baseline, candidate)
    out_md = Path(args.out)
    if out_md.suffix.lower() == ".json":
        raise SystemExit("--out is the markdown report path; the machine "
                         "summary is written next to it as .json — do not "
                         "pass a .json path")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md, encoding="utf-8")
    _write_json(out_md.with_suffix(".json"), summary)
    print(md)                              # the human-facing copy on stdout
    print(f"report -> {out_md}", file=sys.stderr)
    print(f"summary -> {out_md.with_suffix('.json')}", file=sys.stderr)
    return 0                               # always 0: the human decides


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="_evolution_bench.py",
        description="COSMOS Evolution benchmark: run the crew-scheduling "
                    "battery (webapp/jobs.py command mirror) and compare "
                    "metrics documents.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run the battery, write metrics JSON")
    p_run.add_argument("--profile", required=True, choices=sorted(PROFILES),
                       help="quick: inst 1-4, 10 min / 300 s; "
                            "full: inst 1-7, 75 min / 600 s")
    p_run.add_argument("--out", required=True,
                       help="metrics JSON path; per-instance logs land "
                            "next to it")
    p_run.add_argument("--repo", default=None,
                       help="target repo checkout (default: this script's "
                            "own directory)")

    p_cmp = sub.add_parser("compare",
                           help="baseline vs candidate metrics -> markdown "
                                "report + machine summary JSON")
    p_cmp.add_argument("--baseline", required=True)
    p_cmp.add_argument("--candidate", required=True)
    p_cmp.add_argument("--out", required=True,
                       help="markdown report path; <out>.json sibling gets "
                            "the machine summary")

    args = ap.parse_args(argv)
    if args.cmd == "run":
        return _cmd_run(args)
    return _cmd_compare(args)


if __name__ == "__main__":
    raise SystemExit(main())
