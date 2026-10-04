"""What-if scenarios (#11): clone isolation, per-scenario rules for jobs,
cost overrides, comparison, API."""
import importlib
import json

import pytest
from fastapi.testclient import TestClient

from webapp import costing, registry, scenarios
from _stand_identity import ADMIN_EMAIL, ADMIN_SLUG, LEGACY_UPLOAD_ID


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """Isolate every mutable store; keep the real seed payloads readable.
    Scenario payload clones must live UNDER the repo (registry paths are
    relative_to REPO_ROOT), so UPLOADS_DIR stays real — but every scenario
    file this test creates is removed afterwards (tenancy-test pattern)."""
    monkeypatch.setattr(scenarios, "SCENARIOS_PATH",
                        tmp_path / "scenarios.json")
    from webapp import config_scope
    monkeypatch.setitem(config_scope.SCENARIO_DIRS, config_scope.DOMAIN_RULES,
                        tmp_path / "scenario-rules")
    monkeypatch.setattr(registry, "OWNERSHIP_PATH",
                        tmp_path / "ownership.json")
    # P0-03: короткий grandfathered-слаг личности, как на реальном стенде.
    (tmp_path / "ownership.json").write_text(json.dumps(
        {LEGACY_UPLOAD_ID: ADMIN_EMAIL}), encoding="utf-8")
    monkeypatch.setattr(registry, "LLM_SCOPE_PATH",
                        tmp_path / "llm_scope.json")
    monkeypatch.setitem(config_scope.INSTALL_FILES, config_scope.DOMAIN_COST,
                        tmp_path / "cc.json")
    monkeypatch.setitem(config_scope.INSTALL_FILES, config_scope.DOMAIN_RULES,
                        tmp_path / "airline_default.json")
    monkeypatch.setitem(config_scope.INSTANCE_DIRS, config_scope.DOMAIN_RULES,
                        tmp_path / "instance-rules")
    updir = registry.UPLOADS_DIR / ADMIN_SLUG
    before = set(updir.glob("scenario-*.json")) if updir.exists() else set()
    yield tmp_path
    if updir.exists():
        for p in updir.glob("scenario-*.json"):
            if p not in before:
                p.unlink(missing_ok=True)


EMAIL = ADMIN_EMAIL


def _mk(iso_dir):
    base = registry.get_instance("gerad-1")
    return scenarios.create_scenario(
        EMAIL, "test what if", base,
        {"min_sit_min": 45, "max_landings_per_duty": 4},
        {"block_hour_rate": 150})


def test_create_clones_and_registers(iso):
    created = _mk(iso)
    sid = created["id"]
    # Label carries a unique suffix so a delete+recreate cannot alias the
    # prior run's artifacts (audit 2026-07-15).
    assert sid.startswith(f"upload-{ADMIN_SLUG}--scenario-test-what-if-")
    inst = registry.get_instance(sid)
    assert inst is not None and inst["owner"] == EMAIL
    # payload cloned: same leg count as base
    base = registry.get_instance("gerad-1")
    assert inst["legs"] == base["legs"]
    # scenario inherits the base's L/C attribute
    assert inst["llm_scope"] == base["llm_scope"]
    # rules file exists, full set with overrides applied
    rp = scenarios.rules_path_for(sid)
    assert rp is not None
    rules = json.loads(rp.read_text(encoding="utf-8"))["parameters"]
    assert rules["min_sit_min"] == 45 and rules["max_landings_per_duty"] == 4
    assert rules["min_rest_min"] > 0          # defaults filled in


def test_jobs_env_uses_scenario_rules(iso):
    created = _mk(iso)
    from webapp import jobs
    inst = registry.get_instance(created["id"])
    env = jobs._base_env(inst)
    assert env["CREW_RULES_CONFIG"].endswith(f"{created['id']}.json")
    # strict mode: scenario sit/rest must beat the stage-1 v2 presets
    assert env["CREW_RULES_STRICT"] == "1"
    # non-scenario instances keep the global config, no strict flag
    env2 = jobs._base_env(registry.get_instance("gerad-1"))
    assert env2.get("CREW_RULES_CONFIG", "").endswith(
        "airline_default.json") or "CREW_RULES_CONFIG" not in env2
    assert "CREW_RULES_STRICT" not in env2


def test_stage1_command_mirrors_gerad_base_lane(iso):
    """A scenario cloned from a GERAD reference runs the base's lane
    (anchored bases + gerad profile), so the objective diff reflects the
    rule change, not a different run configuration."""
    created = _mk(iso)
    from webapp import jobs
    inst = registry.get_instance(created["id"])
    cmd = jobs._build_commands("stage1", inst, {"budget_min": 5})[0]
    assert "--bases" in cmd and "BASE1,BASE2,BASE3" in cmd
    i = cmd.index("--profile")
    assert cmd[i + 1] == "gerad"
    assert "--payload" in cmd            # runs on the CLONE, not the seed


def test_apply_profile_v2_respects_strict(monkeypatch):
    """v2 presets (sit 60 / rest 8h) must yield to the configured rules
    when CREW_RULES_STRICT=1; without the flag v2 keeps its historical
    byte-identical behavior."""
    import _gerad_colgen as cg
    monkeypatch.setattr(cg, "MIN_GROUND_TIME_MIN", 45)
    monkeypatch.setattr(cg, "MIN_OVERNIGHT_REST_MIN", 555)
    monkeypatch.setattr(cg, "DEAD_ZONE_ON", False)
    monkeypatch.setenv("CREW_RULES_STRICT", "1")
    cg.apply_profile("v2")
    assert cg.MIN_GROUND_TIME_MIN == 45          # scenario value survives
    assert cg.MIN_OVERNIGHT_REST_MIN == 555
    assert cg.DEAD_ZONE_ON is True               # profile semantics stay
    monkeypatch.delenv("CREW_RULES_STRICT")
    cg.apply_profile("v2")
    assert cg.MIN_GROUND_TIME_MIN == 60          # historical presets
    assert cg.MIN_OVERNIGHT_REST_MIN == 8 * 60


def test_cost_override_applies_only_to_scenario(iso):
    created = _mk(iso)
    assert scenarios.cost_overrides_for(created["id"]) == \
        {"block_hour_rate": 150}
    assert scenarios.cost_overrides_for("gerad-1") == {}


def test_create_scenario_cyrillic_name(iso):
    """Prod bug 2026-07-15: 'Тестовый сценарий' -> HTTP 422 'empty slug'.
    Cyrillic must transliterate; unsupported scripts must hash-fallback."""
    base = registry.get_instance("gerad-1")
    created = scenarios.create_scenario(EMAIL, "Тестовый сценарий", base, {})
    assert created["id"].startswith(
        f"upload-{ADMIN_SLUG}--scenario-testovyj-scenarij-")
    assert created["name"] == "Тестовый сценарий"
    assert registry.get_instance(created["id"]) is not None
    created2 = scenarios.create_scenario(EMAIL, "سيناريو تجريبي", base, {})
    # Arabic is not in the transliteration table -> stable digest slug
    assert created2["id"].startswith(f"upload-{ADMIN_SLUG}--scenario-n")
    assert registry.get_instance(created2["id"]) is not None


def test_validate_rules_rejects_bad(iso):
    base = registry.get_instance("gerad-1")
    with pytest.raises(scenarios.ScenarioError):
        scenarios.create_scenario(EMAIL, "bad rules", base,
                                  {"nonsense_param": 5})
    with pytest.raises(scenarios.ScenarioError):
        scenarios.create_scenario(EMAIL, "bad value", base,
                                  {"min_sit_min": -1})


def test_compare_diffs_and_sides(iso):
    created = _mk(iso)
    cmp_ = scenarios.compare(created["id"])
    assert cmp_["base"]["instance"] == "gerad-1"
    diff = {d["param"]: d for d in cmp_["rules_diff"]}
    assert diff["min_sit_min"]["base"] == 30
    assert diff["min_sit_min"]["scenario"] == 45
    assert "max_landings_per_duty" in diff
    cost = {d["param"]: d for d in cmp_["cost_diff"]}
    assert cost["block_hour_rate"]["scenario"] == 150
    # base has a catalog -> its KPI side is present; the fresh scenario
    # has no catalog yet -> KPI None
    assert cmp_["base"]["kpi"] is not None
    assert cmp_["scenario"]["kpi"] is None


def test_compare_baselines_on_effective_global_rules(iso):
    """Audit 2026-07-15: once an admin edits a global rule, compare()'s base
    side must reflect the ACTUAL rules the base runs under, not code
    defaults — no phantom diff, real diffs not masked."""
    import json as _json
    from webapp import config_scope
    config_scope.install_path(config_scope.DOMAIN_RULES).write_text(
        _json.dumps({"parameters": {"min_sit_min": 45}}), encoding="utf-8")
    base = registry.get_instance("gerad-1")
    # scenario reverts sit to 30 while the global is 45
    created = scenarios.create_scenario(EMAIL, "revert sit", base,
                                        {"min_sit_min": 30})
    # create baselines the full set on the global config, not DEFAULTS:
    rp = scenarios.rules_path_for(created["id"])
    full = json.loads(rp.read_text(encoding="utf-8"))["parameters"]
    assert full["min_sit_min"] == 30            # explicit override
    cmp_ = scenarios.compare(created["id"])
    diff = {d["param"]: d for d in cmp_["rules_diff"]}
    assert diff["min_sit_min"]["base"] == 45    # base runs the global 45
    assert diff["min_sit_min"]["scenario"] == 30
    # a scenario that KEEPS the global value shows NO phantom diff
    created2 = scenarios.create_scenario(EMAIL, "keep sit", base,
                                         {"min_sit_min": 45})
    cmp2 = scenarios.compare(created2["id"])
    assert all(d["param"] != "min_sit_min" for d in cmp2["rules_diff"])


def test_scenario_of_scenario_runs_root_lane(iso):
    """A scenario based on another scenario still runs the GERAD root's
    anchored lane (audit 2026-07-15)."""
    from webapp import jobs
    base = registry.get_instance("gerad-1")
    s1 = scenarios.create_scenario(EMAIL, "s1", base, {"min_sit_min": 40})
    s1_inst = registry.get_instance(s1["id"])
    s2 = scenarios.create_scenario(EMAIL, "s2", s1_inst, {"min_sit_min": 50})
    assert scenarios.root_base_instance(s2["id"])["id"] == "gerad-1"
    cmd = jobs._build_commands("stage1", registry.get_instance(s2["id"]),
                               {"budget_min": 5})[0]
    assert "--bases" in cmd and cmd[cmd.index("--profile") + 1] == "gerad"


def test_create_rejects_non_object_overrides(iso):
    base = registry.get_instance("gerad-1")
    with pytest.raises(scenarios.ScenarioError):
        scenarios.create_scenario(EMAIL, "bad", base, {}, "not-a-dict")


def test_delete_scenario_owner_only(iso):
    created = _mk(iso)
    sid = created["id"]
    assert scenarios.delete_scenario(sid, "someone@else.com",
                                     is_admin=False) is False
    assert scenarios.delete_scenario(sid, EMAIL, is_admin=False) is True
    assert registry.get_instance(sid) is None
    assert scenarios.meta_for(sid) is None


def test_delete_purges_label_artifacts(iso):
    """delete_scenario removes the scenario's own label-keyed stage
    artifacts so a later same-name scenario cannot inherit them. The label
    is unique, so writing under the real best dir is collision-safe."""
    created = _mk(iso)
    label = registry.get_instance(created["id"])["label"]
    registry.BEST_DIR.mkdir(parents=True, exist_ok=True)
    art = registry.BEST_DIR / f"gerad_best_catalog_{label}.json"
    meta = registry.BEST_DIR / f"meta_{label}.json"
    art.write_text("[]", encoding="utf-8")
    meta.write_text("{}", encoding="utf-8")
    try:
        assert scenarios.delete_scenario(created["id"], EMAIL,
                                         is_admin=False) is True
        assert not art.exists() and not meta.exists()
    finally:
        art.unlink(missing_ok=True)
        meta.unlink(missing_ok=True)


def test_slug_length_capped(iso):
    base = registry.get_instance("gerad-1")
    created = scenarios.create_scenario(EMAIL, "z" * 400, base, {})
    # id stays bounded despite the 400-char name
    assert len(created["id"]) < 120
    assert registry.get_instance(created["id"]) is not None


@pytest.fixture()
def client(iso, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", EMAIL)
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", iso / "users.db")
    users.ensure_db()
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def test_api_lifecycle(client):
    r = client.post("/api/scenarios", json={
        "name": "test what if", "base_instance_id": "gerad-1",
        "rules": {"min_sit_min": 45},
        "cost_overrides": {"block_hour_rate": 150}})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    lst = client.get("/api/scenarios").json()["scenarios"]
    assert any(s["id"] == sid for s in lst)
    cmp_ = client.get(f"/api/scenarios/{sid}/compare")
    assert cmp_.status_code == 200
    assert cmp_.json()["rules_diff"]
    # scenario is a real instance: visible in the registry list
    inst = client.get(f"/api/instances/{sid}")
    assert inst.status_code == 200
    assert client.delete(f"/api/scenarios/{sid}").status_code == 200
    assert client.get(f"/api/scenarios/{sid}/compare").status_code == 404


def test_api_create_bad_base_404(client):
    r = client.post("/api/scenarios", json={
        "name": "x", "base_instance_id": "upload-nope"})
    assert r.status_code == 404
