"""«Прогоны» data layer: metric + convergence parsing from run logs, run
record normalization, and a Panel dashboard build smoke test."""
from webapp import runs

STAGE1_LOG = (
    "=== run: .venv\\Scripts\\python.exe _gerad_colgen.py 0 --payload x "
    "--profile v2 --name e2e-air ===\n"
    "=== Stage-1 via COLUMN GENERATION [v2, duty-fixed]: e2e-air (9 legs) ===\n"
    "  it   1: LP       3485.0  +     2 cols (best rc  -120.0)  pricing 0.0s  pool 9\n"
    "  it   2: LP       3245.0  +     0 cols (best rc     0.0)  pricing 0.0s  pool 11\n"
    "  CG done: 2 iters, pool 11, LP bound 3245.0 (valid), pricing 0s + LP 0s\n"
    "  CP-SAT: 7 selected (OPTIMAL) in 0.4s  obj=3245\n")

STAGE2_LOG = (
    "  assignment trajectory -> gerad_stage2_assignment_e2e-air.json "
    "[INFEASIBLE_INCUMBENT]\n"
    "  !! result_status=INFEASIBLE_INCUMBENT — не наряд; "
    "trajectory_violations=5, missing=3\n"
    "  stage2=0hard/-30000soft  uniform(GERAD2)=0hard/-30160soft  "
    "direct-uniform=None\n"
    "  pref_hits=0/0  vacation_violations=0  unassigned=3  (66s)\n")


def test_parse_stage1_metrics():
    m = runs.parse_metrics(STAGE1_LOG, "stage1")
    assert m["objective"] == 3245.0
    assert m["lp_bound"] == 3245.0
    assert m["gap_pct"] == 0.0
    assert m["iters"] == 2 and m["pool"] == 11
    assert m["verdict"] == "OPTIMAL"


def test_parse_stage2_metrics():
    m = runs.parse_metrics(STAGE2_LOG, "stage2")
    assert m["verdict"] == "INFEASIBLE_INCUMBENT"
    assert m["unassigned"] == 3
    assert m["trajectory_violations"] == 5 and m["missing_legs"] == 3
    assert m["wall_s"] == 66.0


def test_gap_computed_when_obj_above_bound():
    log = "  CG done: LP bound 100.0 (valid)\n  CP-SAT: (OPTIMAL) obj=110\n"
    m = runs.parse_metrics(log, "stage1")
    assert m["objective"] == 110.0 and m["lp_bound"] == 100.0
    assert m["gap_pct"] == 10.0


def test_run_series_stage1(monkeypatch):
    from webapp import jobs
    monkeypatch.setattr(jobs, "read_log", lambda jid: STAGE1_LOG)
    s = runs.run_series("stage1-x")
    assert s["kind"] == "stage1-cg"
    assert [p["it"] for p in s["points"]] == [1, 2]
    assert [p["lp"] for p in s["points"]] == [3485.0, 3245.0]
    assert s["points"][1]["pool"] == 11


# Real prod format (found 2026-07-15): counters carry thousands separators,
# the live feed and the final table both print the same 'it N' lines, and
# base-anchored runs print a slack-inflated LP bound ABOVE the objective.
ANCHORED_LOG = (
    "  it   1: LP  101300000.0  +  3862 cols (best rc -1.0)  pool 3,862\n"
    "  it   2: LP     361963.9  +   466 cols (best rc -98536.0)  pricing"
    "    0.1s [capped]  pool 5,219\n"
    "  it   1: LP  101300000.0  +  3862 cols (best rc -1.0)  pool 3,862\n"
    "  it   2: LP     361963.9  +   466 cols (best rc -98536.0)  pricing"
    "    0.1s [capped]  pool 5,219\n"
    "  CG done: 2 iters, pool 150,848, LP bound 165096.0 (valid)\n"
    "  CP-SAT: 111 selected (OPTIMAL) in 29.0s  obj=148497\n")


def test_parse_thousands_separators_and_dedup():
    m = runs.parse_metrics(ANCHORED_LOG, "stage1")
    assert m["pool"] == 5219            # was 5 before the comma fix
    assert m["iters"] == 2              # duplicated 'it' lines collapse
    assert m["objective"] == 148497.0


def test_negative_gap_is_nulled_as_uncertified():
    """Anchored runs price artificial slack into the printed LP bound —
    a bound above the objective yields a meaningless negative gap."""
    m = runs.parse_metrics(ANCHORED_LOG, "stage1")
    assert m["lp_bound"] == 165096.0
    assert m["gap_pct"] is None


def test_run_series_dedups_repeated_iterations(monkeypatch):
    from webapp import jobs
    monkeypatch.setattr(jobs, "read_log", lambda jid: ANCHORED_LOG)
    s = runs.run_series("stage1-x")
    assert [p["it"] for p in s["points"]] == [1, 2]
    assert s["points"][0]["pool"] == 3862
    assert s["points"][1]["pool"] == 5219


def test_run_series_empty_for_stage2(monkeypatch):
    from webapp import jobs
    monkeypatch.setattr(jobs, "read_log", lambda jid: STAGE2_LOG)
    s = runs.run_series("stage2-x")
    assert s["kind"] == "none" and s["points"] == []


def test_list_runs_normalizes_and_filters(monkeypatch):
    from webapp import jobs
    jobrows = [
        {"id": "stage1-a", "type": "stage1", "instance_id": "gerad-1",
         "owner": "a@b.c", "status": "succeeded", "exit_code": 0,
         "started_at": "2026-07-13 01:00:00", "ended_at": "2026-07-13 01:02:00"},
        {"id": "stage2-b", "type": "stage2", "instance_id": "bts-day",
         "owner": "z@z.z", "status": "succeeded", "exit_code": 1,
         "started_at": "2026-07-13 02:00:00", "ended_at": "2026-07-13 02:10:05"},
    ]
    logs = {"stage1-a": STAGE1_LOG, "stage2-b": STAGE2_LOG}
    monkeypatch.setattr(jobs, "list_jobs", lambda: jobrows)
    monkeypatch.setattr(jobs, "read_log", lambda jid: logs.get(jid, ""))
    allr = runs.list_runs()
    assert {r["id"] for r in allr} == {"stage1-a", "stage2-b"}
    a = next(r for r in allr if r["id"] == "stage1-a")
    assert a["objective"] == 3245.0 and a["verdict"] == "OPTIMAL"
    assert a["wall_s"] == 66.0 or a["wall_s"] == 120.0  # log wall or time-delta
    b = next(r for r in allr if r["id"] == "stage2-b")
    assert b["verdict"] == "INFEASIBLE_INCUMBENT" and b["unassigned"] == 3
    # owner filter
    mine = runs.list_runs(owner="a@b.c")
    assert [r["id"] for r in mine] == ["stage1-a"]


def test_dashboard_builds(monkeypatch):
    from webapp import runs as runs_mod
    monkeypatch.setattr(runs_mod, "list_runs", lambda owner=None: [])
    from webapp import runs_dashboard
    dash = runs_dashboard.make_dashboard()
    # a Panel layout object with children — builds without raising
    assert hasattr(dash, "objects") and len(dash.objects) >= 2


def test_dashboard_i18n_key_parity():
    """Every language must define exactly the same keys — a stray key in one
    locale (or a missing one) would KeyError at render time."""
    from webapp import runs_dashboard as rd
    ref = set(rd._STRINGS["ru"])
    assert set(rd._LANGS) == set(rd._STRINGS) == set(rd._TAB_JOBS) == set(rd._TAB_FORMAT)
    for lg in rd._LANGS:
        assert set(rd._STRINGS[lg]) == ref, f"{lg}: key set differs from ru"


def test_dashboard_i18n_covers_every_used_key():
    """The keys the code actually looks up must exist in every locale."""
    from webapp import runs_dashboard as rd
    needed = ([f"col.{c}" for c in rd._COLS]
              + [f"kpi.{c}" for c in rd._KPI_COLS]
              + ["conv.title", "conv.x", "conv.y", "conv.empty",
                 "mix.title", "mix.pay", "mix.perdiem", "mix.hotel", "mix.yaxis",
                 "kpi.heading", "kpi.note", "note.empty", "note.no_stage1",
                 "note.default", "refresh", "runs.heading", "tab.runs", "tab.kpi"])
    for lg in rd._LANGS:
        S = rd._strings(lg)
        for k in needed:
            assert k in S, f"{lg}: missing key {k}"


def test_dashboard_i18n_placeholders_and_fallback():
    """_strings() resolves the {jobs}/{fmt} tab-label placeholders, keeps the
    markdown bold markers, and falls back to ru for an unknown language."""
    from webapp import runs_dashboard as rd
    for lg in rd._LANGS:
        S = rd._strings(lg)
        for k, v in S.items():
            assert "{jobs}" not in v and "{fmt}" not in v, f"{lg}/{k}: unresolved"
        assert rd._TAB_JOBS[lg] in S["note.empty"]
        assert rd._TAB_FORMAT[lg] in S["kpi.note"]
        assert S["note.no_stage1"].count("**") == 2
        assert S["note.default"].count("**") == 2
        assert S["tab.runs"] == rd._STRINGS[lg]["tab.runs"]
    # unknown language falls back to the Russian source strings
    assert rd._strings("zz")["tab.runs"] == rd._STRINGS["ru"]["tab.runs"]
