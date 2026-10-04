"""Unit tests for _evolution_bench.py — the COSMOS Evolution benchmark.

NO real battery runs here (they take hours): only the pure parts —
command construction (asserted against webapp/jobs.py itself, so drift
between the bench and the web app's job runner fails the test), metric
extraction from synthetic artifact fixtures, and the compare logic
(polarity, verdict, highlights, markdown compactness)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _evolution_bench as eb            # noqa: E402
import webapp.jobs as jobs               # noqa: E402


def _gerad_inst(n: int) -> dict:
    """Minimal instance dict accepted by webapp.jobs._build_commands."""
    return {"id": f"gerad-{n}", "label": f"inst{n}", "source": "gerad"}


# ------------------------------------------------- command construction

def test_stage1_cmd_matches_webapp_jobs():
    # Expectation derived from webapp/jobs.py itself — a change to the
    # webapp's stage-1 command line must fail this test, not drift silently.
    for n, budget_min in ((1, 10), (4, 75)):
        expected = jobs._build_commands(
            "stage1", _gerad_inst(n), {"budget_min": budget_min})[0]
        actual = eb.build_stage1_cmd(n, budget_min, jobs.REPO_ROOT)
        assert actual == expected


def test_stage2_cmd_matches_webapp_jobs():
    for n, budget_s, seed in ((2, 300, 7), (7, 600, 7)):
        expected = jobs._build_commands(
            "stage2", _gerad_inst(n), {"budget_s": budget_s, "seed": seed})[0]
        actual = eb.build_stage2_cmd(n, budget_s, seed, jobs.REPO_ROOT)
        assert actual == expected


@pytest.mark.real_airline_config
def test_env_and_port_pool_match_webapp_jobs():
    # CREW_RULES_CONFIG wiring must be exactly what the webapp jobs get.
    assert (eb.build_env(jobs.REPO_ROOT).get("CREW_RULES_CONFIG")
            == jobs._base_env().get("CREW_RULES_CONFIG"))
    assert eb.STAGE2_PORT_POOL == jobs.STAGE2_PORT_POOL


def test_cmd_builders_reject_nonpositive_budgets():
    with pytest.raises(ValueError):
        eb.build_stage1_cmd(1, 0, jobs.REPO_ROOT)
    with pytest.raises(ValueError):
        eb.build_stage2_cmd(1, -5, 7, jobs.REPO_ROOT)


# --------------------------------------------------- metric extraction

def test_extract_stage1_metrics_from_fixture_artifacts():
    meta = {"ip_objective": 116589, "proven_impossible_legs": [],
            "run_id": "r1", "fingerprint": "f"}
    # The catalog dump carries a DIFFERENT objective: meta must win
    # (webapp/results.py _stage1 order).
    selected = {"ip_objective": 999999, "ip_status": "OPTIMAL",
                "selected": [
                    {"fns": ["A", "B"], "active_fns": ["A"], "cost": 1},
                    {"fns": ["B", "C"], "cost": 2},        # no active_fns
                ]}
    record = {"schema": "stage1/v2", "label": "inst1",
              "initial_budget_result": {
                  "lp_bound": 116589.0, "lp_bound_valid": True,
                  "gap_vs_lp_pct": 0.0, "ip_objective": 116589,
                  "ip_status": "OPTIMAL", "selected": 201,
                  "total_s": 206.6}}
    m = eb.extract_stage1_metrics(meta, selected, record, wall_s=123.4)
    assert m["ip_objective"] == 116589
    # Union of active_fns (fallback fns): {A} | {B, C} = 3 legs.
    assert m["covered"] == 3
    assert m["lp_bound"] == 116589.0
    assert m["lp_bound_valid"] is True
    assert m["gap_vs_lp_pct"] == 0.0
    assert m["wall_s"] == 123.4
    assert "error" not in m


def test_extract_stage1_metrics_all_sources_missing_is_error():
    m = eb.extract_stage1_metrics(None, None, None, wall_s=1.0)
    assert "error" in m


def test_extract_stage2_metrics_last_row_wins():
    old = {"inst": 1, "result_status": "INFEASIBLE_INCUMBENT",
           "stage2_score": "-1hard/0soft", "unassigned": 5,
           "vacation_violations": 2, "pref_hits": 10,
           "trajectory_violations": 3, "missing_legs": 4, "wall_s": 100.0}
    new = {"inst": 1, "result_status": "OK",
           "stage2_score": "0hard/18400soft", "unassigned": 0,
           "vacation_violations": 0, "pref_hits": 184,
           "trajectory_violations": 0, "missing_legs": 0, "wall_s": 610.2,
           "seed": 7, "budget_s": 600}
    journal = [old, {"inst": 2, "result_status": "OK"}, new, "junk-row"]
    m = eb.extract_stage2_metrics(journal, 1)
    assert m == {"result_status": "OK", "stage2_score": "0hard/18400soft",
                 "unassigned": 0, "vacation_violations": 0, "pref_hits": 184,
                 "trajectory_violations": 0, "missing_legs": 0,
                 "wall_s": 610.2}
    assert eb.extract_stage2_metrics(journal, 5) is None
    assert eb.extract_stage2_metrics(None, 1) is None


# ------------------------------------------------------------- compare

def _metrics_doc(instances, commit="abc1234"):
    return {"schema": "evolution-bench/v1", "profile": "quick",
            "commit": commit, "started_at": "2026-07-13 10:00:00",
            "finished_at": "2026-07-13 12:00:00", "instances": instances}


def _inst_entry(ip_objective=58787, pref_hits=100, **overrides):
    stage1 = {"ip_objective": ip_objective, "lp_bound": 58000.0,
              "lp_bound_valid": True, "gap_vs_lp_pct": 1.36,
              "covered": 1013, "wall_s": 600.0}
    stage2 = {"result_status": "OK", "stage2_score": "0hard/18400soft",
              "unassigned": 0, "vacation_violations": 0,
              "pref_hits": pref_hits, "trajectory_violations": 0,
              "missing_legs": 0, "wall_s": 300.0}
    stage1.update(overrides.get("stage1", {}))
    stage2.update(overrides.get("stage2", {}))
    return {"stage1": stage1, "stage2": stage2}


def test_compare_mixed_better_worse_na(tmp_path):
    baseline = _metrics_doc({"inst1": _inst_entry()})
    # ip_objective лучше; missing_legs ХУЖЕ (жёсткая покрытийная метрика —
    # гейтится); pref_hits хуже, но мягкая — совещательная; gap_vs_lp_pct
    # отсутствует у кандидата (дыра схемы).
    cand_entry = _inst_entry(ip_objective=58121, pref_hits=90)
    cand_entry["stage2"]["missing_legs"] = 3
    del cand_entry["stage1"]["gap_vs_lp_pct"]      # n/a on candidate side
    candidate = _metrics_doc({"inst1": cand_entry}, commit="def5678")

    md, summary = eb.compare_data(baseline, candidate)

    # Регресс — от ЖЁСТКОЙ метрики (missing_legs) и дыры схемы. pref_hits
    # ухудшилась, но как мягкая стохастическая в вердикт НЕ входит
    # (проверка находки 27: одно зерно — не сигнал).
    assert summary["verdict"] == "regressed"
    assert summary["better"] == 1
    assert summary["worse"] == 1                 # только missing_legs
    assert summary["soft_changes"] == 1          # pref_hits — совещательно
    assert summary["metric_holes"] == 1
    # 14 метрик: 1 better + 1 worse + 1 soft + 1 n/a -> 10 equal.
    assert summary["equal"] == 10
    assert any(h.startswith("inst1 stage1 ip_objective 58787 -> 58121")
               and "-1.1%" in h for h in summary["highlights"])
    assert any("missing_legs" in h for h in summary["highlights"])
    # pref_hits НЕ в highlights — она вне вердикта
    assert not any("pref_hits" in h for h in summary["highlights"])
    assert len(summary["highlights"]) == 2

    assert "REGRESSED" in md
    assert "REGRESSIONS PRESENT" in md
    assert "METRIC SCHEMA HOLES" in md
    assert "ADVISORY" in md
    assert len(md.strip().splitlines()) < 60

    # File-level CLI: writes report.md + sibling report.json, exits 0.
    b_path, c_path = tmp_path / "a.json", tmp_path / "b.json"
    b_path.write_text(json.dumps(baseline), encoding="utf-8")
    c_path.write_text(json.dumps(candidate), encoding="utf-8")
    out_md = tmp_path / "report.md"
    rc = eb.main(["compare", "--baseline", str(b_path),
                  "--candidate", str(c_path), "--out", str(out_md)])
    assert rc == 0
    assert out_md.read_text(encoding="utf-8") == md
    machine = json.loads((tmp_path / "report.json")
                         .read_text(encoding="utf-8"))
    assert machine == summary


def test_soft_stage2_metrics_are_advisory_not_gated():
    """Кандидат ухудшил ТОЛЬКО мягкие метрики этапа 2 — это не регресс.

    Ровно случай находки 27: vacation_violations и pref_hits «просели» на
    одном зерне, но проверка показала размах 88 на том же каталоге от
    зерна. Жёсткое «любое ухудшение = regressed» ложно браковало годную
    находку; теперь мягкие метрики совещательны."""
    baseline = _metrics_doc({"inst1": _inst_entry(pref_hits=347)})
    cand = _inst_entry(pref_hits=346)             # −1, мягкая
    cand["stage2"]["vacation_violations"] = 25    # база 0 -> 25, мягкая
    candidate = _metrics_doc({"inst1": cand}, commit="def5678")

    _md, summary = eb.compare_data(baseline, candidate)
    assert summary["verdict"] == "no_gain"        # НЕ regressed
    assert summary["worse"] == 0
    assert summary["soft_changes"] == 2           # vacation + pref_hits


def test_hard_stage2_coverage_regression_still_gates():
    """А ухудшение ЖЁСТКОЙ покрытийной метрики этапа 2 — настоящий регресс.

    Демоция мягких метрик не должна ослепить гейт: unassigned/missing_legs/
    trajectory_violations про легальность и покрытие, их рост гейтится."""
    baseline = _metrics_doc({"inst1": _inst_entry()})
    cand = _inst_entry()
    cand["stage2"]["unassigned"] = 5              # база 0 -> 5, жёсткая
    candidate = _metrics_doc({"inst1": cand}, commit="def5678")

    _md, summary = eb.compare_data(baseline, candidate)
    assert summary["verdict"] == "regressed"
    assert summary["worse"] == 1


def test_compare_equal_files_is_no_gain():
    doc = _metrics_doc({"inst1": _inst_entry(), "inst2": _inst_entry()})
    md, summary = eb.compare_data(doc, doc)
    assert summary["verdict"] == "no_gain"
    assert summary["better"] == 0
    assert summary["worse"] == 0
    assert summary["highlights"] == []
    assert "NO GAIN" in md
    assert "REGRESSIONS PRESENT" not in md


def test_compare_polarity_table():
    # Every declared metric, both directions, via the classifier itself.
    for metric in eb.LOWER_BETTER:
        assert eb._classify(metric, 2, 1) == "better"
        assert eb._classify(metric, 1, 2) == "worse"
        assert eb._classify(metric, 1, 1) == "equal"
    for metric in eb.HIGHER_BETTER:
        assert eb._classify(metric, 1, 2) == "better"
        assert eb._classify(metric, 2, 1) == "worse"
    assert eb._classify("lp_bound_valid", False, True) == "better"
    assert eb._classify("lp_bound_valid", True, False) == "worse"
    assert eb._classify("result_status", "INFEASIBLE_INCUMBENT", "OK") \
        == "better"
    assert eb._classify("result_status", "OK", "INFEASIBLE_INCUMBENT") \
        == "worse"
    assert eb._classify("ip_objective", None, 5) == "n/a"
    assert eb._classify("lp_bound", 1, 2) == "info"
    with pytest.raises(ValueError):
        eb._classify("ip_objective", "not-a-number", 5)


def test_compare_stage_errors_and_missing_instances_do_not_crash():
    baseline = _metrics_doc({"inst1": _inst_entry(),
                             "inst2": _inst_entry(),
                             "inst3": _inst_entry()})
    candidate = _metrics_doc({
        "inst1": _inst_entry(),
        # stage2 crashed on the candidate side; stage1 improved.
        "inst2": {"stage1": _inst_entry(ip_objective=58000)["stage1"],
                  "stage2": {"error": "stage2 exit code 2 (see log)"}},
        # inst3 missing entirely on the candidate side.
    })
    md, summary = eb.compare_data(baseline, candidate)
    # H16 (corrected): a candidate that crashed/vanished where the baseline
    # succeeded is a fatal regression — a stray improved metric elsewhere
    # (inst2 stage1) must NOT relabel it "improved".
    assert summary["verdict"] == "regressed"
    assert summary["candidate_errors"] >= 1
    assert "inst2 stage2 candidate error" in md
    assert "inst3" in md                        # noted as missing
    assert len(md.strip().splitlines()) < 60


def test_compare_markdown_stays_compact_when_everything_changes():
    base_insts, cand_insts = {}, {}
    for n in range(1, 8):
        base_insts[f"inst{n}"] = _inst_entry()
        cand_insts[f"inst{n}"] = _inst_entry(
            ip_objective=50000 + n, pref_hits=90 + n,
            stage1={"covered": 999, "wall_s": 500.0, "gap_vs_lp_pct": 9.9,
                    "lp_bound": 40000.0, "lp_bound_valid": False},
            stage2={"result_status": "INFEASIBLE_INCUMBENT",
                    "unassigned": 3, "vacation_violations": 1,
                    "trajectory_violations": 2, "missing_legs": 4,
                    "wall_s": 400.0, "stage2_score": "-1hard/0soft"})
    md, summary = eb.compare_data(_metrics_doc(base_insts),
                                  _metrics_doc(cand_insts))
    assert len(md.strip().splitlines()) < 60    # Telegram budget
    assert len(summary["highlights"]) == 5
    # Status flips outrank numeric moves in the highlights.
    assert ("result_status" in summary["highlights"][0]
            or "lp_bound_valid" in summary["highlights"][0])
    assert any("result_status" in h for h in summary["highlights"])


def test_run_logged_tees_progress_to_stdout(tmp_path, capsys):
    """A child's solver-progress line must land BOTH in the per-instance
    log (full fidelity) AND on the bench's own stdout as
    '[inst1] progress: ...' (the live-feed relay); non-progress lines go
    to the log only."""
    child = ("import sys\n"
             "print('  it   3: LP 100.0  + 5 cols (best rc -1.0) pool 1,000')\n"
             "print('some noisy internal line')\n"
             "sys.stdout.flush()\n")
    log = tmp_path / "bench_inst1.log"
    rc = eb._run_logged([sys.executable, "-c", child], tmp_path,
                        dict(__import__("os").environ), log,
                        append=False, label="inst1")
    assert rc == 0
    out = capsys.readouterr().out
    assert "[inst1] progress:" in out and "it   3: LP 100.0" in out
    assert "some noisy internal line" not in out       # progress-only echo
    logged = log.read_text(encoding="utf-8")
    assert "it   3: LP 100.0" in logged and "some noisy internal line" in logged


def test_prog_re_matches_stage1_and_jvm_phase_lines():
    assert eb._PROG_RE.search(
        "  it   7: LP 116589.0  + 60000 cols (best rc -37.2) pool 431,502")
    assert eb._PROG_RE.search(
        "Construction Heuristic phase (0) ended: best score (-800hard/0soft)")
    assert eb._PROG_RE.search(
        "Local Search phase (1) ended: best score (-230hard/-235799soft)")
    assert not eb._PROG_RE.search("random jackson debug line")


def test_load_metrics_file_fails_loud_on_malformed_input(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        eb.load_metrics_file(bad)
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"schema": "something-else/v9",
                                 "instances": {}}), encoding="utf-8")
    with pytest.raises(SystemExit):
        eb.load_metrics_file(wrong)
    with pytest.raises(SystemExit):
        eb.load_metrics_file(tmp_path / "absent.json")


def test_write_json_retries_transient_permission_error(tmp_path, monkeypatch):
    """On Windows the final rename can transiently fail with WinError 5
    when the destination is momentarily locked (antivirus/indexer/poller).
    _write_json must retry and succeed, not fail the whole benchmark."""
    target = tmp_path / "metrics.json"
    real_replace = eb.Path.replace
    calls = {"n": 0}

    def flaky_replace(self, dst):
        calls["n"] += 1
        if calls["n"] < 3:                      # first two attempts "locked"
            raise PermissionError(5, "Access is denied")
        return real_replace(self, dst)

    monkeypatch.setattr(eb.Path, "replace", flaky_replace)
    monkeypatch.setattr(eb.time, "sleep", lambda *_: None)   # no real backoff
    eb._write_json(target, {"schema": "evolution-bench/v1", "ok": True})
    assert calls["n"] == 3
    assert json.loads(target.read_text(encoding="utf-8"))["ok"] is True


def test_write_json_reraises_when_lock_never_clears(tmp_path, monkeypatch):
    target = tmp_path / "metrics.json"

    def always_locked(self, dst):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(eb.Path, "replace", always_locked)
    monkeypatch.setattr(eb.time, "sleep", lambda *_: None)
    with pytest.raises(PermissionError):
        eb._write_json(target, {"x": 1})


def test_compare_worse_only_is_regressed():
    # Аудит-2 P1-10: худшие метрики раньше давали no_gain (Stage passed).
    # После демоции мягких (2026-07-24): ЖЁСТКИЕ ухудшения (ip_objective,
    # unassigned) считаются в worse и валят вердикт; мягкие (pref_hits,
    # vacation_violations) — совещательны и в worse не входят.
    baseline = _metrics_doc({"inst1": _inst_entry()})
    cand = _inst_entry(ip_objective=60000, pref_hits=50)
    cand["stage2"]["unassigned"] = 5
    cand["stage2"]["vacation_violations"] = 3
    candidate = _metrics_doc({"inst1": cand}, commit="def5678")
    _md, summary = eb.compare_data(baseline, candidate)
    assert summary["worse"] == 2 and summary["better"] == 0
    assert summary["soft_changes"] == 2
    assert summary["verdict"] == "regressed"


def test_compare_empty_candidate_metrics_is_regressed():
    # Пустые (но структурно присутствующие) словари метрик кандидата —
    # дыры схемы, а не тихий no_gain.
    baseline = _metrics_doc({"inst1": _inst_entry()})
    candidate = _metrics_doc({"inst1": {"stage1": {}, "stage2": {}}},
                             commit="def5678")
    _md, summary = eb.compare_data(baseline, candidate)
    assert summary["metric_holes"] > 0
    assert summary["verdict"] == "regressed"


def test_wall_clock_noise_does_not_regress_verdict():
    # Ревью пакета: wall_s недетерминирован — шум времени не должен валить
    # Stage (worse==0 по качеству, тайминги в отдельном бакете).
    baseline = _metrics_doc({"inst1": _inst_entry()})
    cand = _inst_entry()
    cand["stage1"]["wall_s"] = 600.4          # медленнее на 0.4 c
    cand["stage2"]["wall_s"] = 299.7          # быстрее на 0.3 c
    candidate = _metrics_doc({"inst1": cand}, commit="def5678")
    _md, summary = eb.compare_data(baseline, candidate)
    assert summary["worse"] == 0 and summary["better"] == 0
    assert summary["timing_changes"] == 2
    assert summary["verdict"] == "no_gain"


def test_stage2_artifact_completed_manifest_branch(tmp_path, monkeypatch):
    # P0-1: не-OK прогон не переписывает канонический артефакт — свежий
    # per-run манифест метки равносилен завершённому гейту
    import time
    import _evolution_bench as eb
    label = "inst5"
    started = time.time() - 1.0
    # №50 этап 2: манифесты ищутся в каталоге данных — изоляция той же
    # переменной, которой управляется и продукт
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path))
    assert eb._stage2_artifact_completed(tmp_path, label, started) is False
    mdir = tmp_path / "final-report" / "runs" / "roster-manifests" / label
    mdir.mkdir(parents=True)
    (mdir / "run.json").write_text("{}", encoding="utf-8")
    assert eb._stage2_artifact_completed(tmp_path, label, started) is True
