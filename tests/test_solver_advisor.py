"""Phase-1 unit tests for the solver-advisor knowledge base."""
import json

import pytest

from webapp import llm_backend, solver_advisor


@pytest.fixture()
def skill_dir(tmp_path, monkeypatch):
    d = tmp_path / "solver_advisor"
    monkeypatch.setattr(solver_advisor, "SKILL_DIR", d)
    monkeypatch.setattr(solver_advisor, "SKILL_PATH", d / "skill.md")
    monkeypatch.setattr(solver_advisor, "META_PATH", d / "meta.json")
    monkeypatch.setattr(solver_advisor, "HISTORY_DIR", d / "history")
    return d


# --------------------------------------------------------- skill storage

def test_load_skill_seed_when_absent(skill_dir):
    sk = solver_advisor.load_skill()
    assert sk["meta"]["version"] == 0
    assert sk["meta"]["source"] == "seed"
    assert "База знаний" in sk["text"]


def test_save_and_version_and_history(skill_dir):
    m1 = solver_advisor.save_skill("# v1\ncontent one", source="llm",
                                   backend="cloud", model="claude-cli",
                                   evidence=["a.json"], now="2026-07-14T10:00:00")
    assert m1["version"] == 1
    sk = solver_advisor.load_skill()
    assert sk["text"].startswith("# v1")
    assert sk["meta"]["backend"] == "cloud"
    # second save bumps version and snapshots v1 into history
    m2 = solver_advisor.save_skill("# v2\ncontent two", source="manual",
                                   now="2026-07-14T11:00:00")
    assert m2["version"] == 2
    hist = (skill_dir / "history" / "skill-v1.md").read_text(encoding="utf-8")
    assert "content one" in hist
    assert solver_advisor.load_skill()["text"].startswith("# v2")


def test_save_empty_rejected(skill_dir):
    with pytest.raises(ValueError):
        solver_advisor.save_skill("   ", source="llm")


# ------------------------------------------------------ instance descriptor

_FAKE_PAYLOAD = {
    "airports": [{"code": "ATL"}, {"code": "JFK"}, {"code": "LAX"}],
    "employees": [
        {"id": "E1", "name": "Alice Secret", "homeAirport": "ATL"},
        {"id": "E2", "name": "Bob Secret", "homeAirport": "JFK"},
        {"id": "E3", "name": "Carol", "homeAirport": "ATL"},
    ],
    "flights": [
        {"flightNumber": "DL1", "departureDateTime": "2024-08-15T08:00:00"},
        {"flightNumber": "DL2", "departureDateTime": "2024-08-18T09:00:00"},
    ],
}


def test_instance_descriptor_is_non_pii(monkeypatch):
    monkeypatch.setattr(solver_advisor, "instance_payload_path",
                        lambda inst: "ignored")
    monkeypatch.setattr(solver_advisor, "load_payload",
                        lambda path: _FAKE_PAYLOAD)
    inst = {"id": "gerad-4", "source": "gerad", "legs": 200, "employees": 50,
            "slots": 400, "stage2_available": True, "has_best_catalog": True,
            "catalog_objective": 1234, "last_gate_status": "OK",
            "llm_scope": "any"}
    rules = {"min_sit_min": 30, "max_landings_per_duty": 5, "extra": 9}
    d = solver_advisor.instance_descriptor(inst, rules)
    blob = json.dumps(d, ensure_ascii=False)
    # aggregates present
    assert d["n_airports"] == 3 and d["n_home_bases"] == 2
    assert d["gerad_number"] == 4
    assert d["horizon"] == {"from": "2024-08-15", "to": "2024-08-18"}
    assert d["rules"] == {"min_sit_min": 30, "max_landings_per_duty": 5}
    # NO PII: employee names / ids never appear
    assert "Secret" not in blob and "Alice" not in blob and "E1" not in blob


# ------------------------------------------------------------- sanitize

def test_sanitize_params_whitelist_and_bounds():
    raw = {"pricer": "duty", "profile": "bogus", "mp": 10, "seed": -5,
           "price_cap": 600, "final_tl": 99999, "evil": "rm -rf",
           "budget_s": 600.0}
    out = solver_advisor.sanitize_params(raw)
    assert out == {"pricer": "duty", "mp": 10, "price_cap": 600,
                   "budget_s": 600}
    # profile 'bogus' dropped, seed out-of-range dropped, final_tl too big
    # dropped, unknown 'evil' dropped
    assert "evil" not in out and "profile" not in out


def test_sanitize_rejects_bool_for_int():
    assert solver_advisor.sanitize_params({"mp": True}) == {}


# --------------------------------------------------------- parse rec

def test_parse_recommendation_amid_prose():
    text = (
        "Sure, here is my analysis...\n"
        '{"stage1": {"engine": "colgen", "pricer": "duty", "profile": "gerad", '
        '"notes": "ok"}, "stage2": {"engine": "Timefold", "notes": ""}, '
        '"split": "seq", "params": {"mp": 8, "junk": 1}, '
        '"warnings": ["watch stage2"], "rationale": "because reasons"}\n'
        "Hope that helps!")
    rec = solver_advisor.parse_recommendation(text)
    assert rec["stage1"]["pricer"] == "duty"
    assert rec["params"] == {"mp": 8}       # junk stripped
    assert rec["warnings"] == ["watch stage2"]
    assert rec["rationale"] == "because reasons"


def test_parse_recommendation_none_on_garbage():
    assert solver_advisor.parse_recommendation("no json here") is None


# --------------------------------------------------------------- consult

def _inst(scope="any", source="gerad", iid="gerad-4"):
    return {"id": iid, "source": source, "legs": 200, "employees": 50,
            "slots": 400, "stage2_available": True, "has_best_catalog": True,
            "catalog_objective": 1234, "last_gate_status": "OK",
            "llm_scope": scope}


@pytest.fixture()
def no_payload(monkeypatch):
    monkeypatch.setattr(solver_advisor, "instance_payload_path",
                        lambda inst: "x")
    monkeypatch.setattr(solver_advisor, "load_payload",
                        lambda path: _FAKE_PAYLOAD)


def test_consult_llm_success(skill_dir, no_payload, monkeypatch):
    reply = ('{"stage1":{"engine":"colgen","pricer":"legs","profile":"v2",'
             '"notes":"n"},"stage2":{"engine":"TF","notes":""},"split":"s",'
             '"params":{"mp":6},"warnings":[],"rationale":"r"}')
    monkeypatch.setattr(llm_backend, "complete",
                        lambda *a, **k: {"ok": True, "text": reply,
                                         "model": "m", "backend": k["backend"]})
    out = solver_advisor.consult(_inst("any"), {"min_sit_min": 30}, "complete",
                                 backend_request="cloud")
    assert out["source"] == "llm" and out["backend"] == "cloud"
    assert out["recommendation"]["stage1"]["pricer"] == "legs"
    assert out["recommendation"]["params"] == {"mp": 6}
    assert "Secret" not in json.dumps(out, ensure_ascii=False)


def test_consult_falls_back_when_llm_unavailable(skill_dir, no_payload,
                                                 monkeypatch):
    monkeypatch.setattr(llm_backend, "complete",
                        lambda *a, **k: {"ok": False, "error": "down",
                                         "model": "m", "backend": k["backend"]})
    out = solver_advisor.consult(_inst("local-only"), None, "complete")
    assert out["source"] == "fallback" and out["backend"] == "local"
    assert out["llm_error"] == "down"
    # fallback still yields a confirmable pre-fill
    assert out["recommendation"]["params"].get("seed") == 7


def test_consult_local_only_rejects_cloud(skill_dir, no_payload):
    with pytest.raises(llm_backend.ScopeViolation):
        solver_advisor.consult(_inst("local-only"), None, "complete",
                               backend_request="cloud")


# --------------------------------------------------- skill build (phase 2)

def test_clean_skill_markdown_unwraps_fence():
    fenced = "Here you go:\n```markdown\n# Doc\nbody\n```\nthanks"
    assert solver_advisor._clean_skill_markdown(fenced) == "# Doc\nbody"
    assert solver_advisor._clean_skill_markdown("# Plain\nx") == "# Plain\nx"


def test_gather_evidence_reads_public_files(tmp_path, monkeypatch):
    monkeypatch.setattr(solver_advisor, "REPO_ROOT", tmp_path)
    (tmp_path / "final-report").mkdir()
    (tmp_path / "final-report" / "orlib_spp_results.csv").write_text(
        "instance,status,objective\np1,OPTIMAL,42\n", encoding="utf-8")
    (tmp_path / "benchmark_results.csv").write_text(
        "engine,score\ntimefold,10\n", encoding="utf-8")
    monkeypatch.setattr(solver_advisor, "EVIDENCE_FILES",
                        ("final-report/orlib_spp_results.csv",
                         "benchmark_results.csv"))
    digest, used = solver_advisor.gather_evidence()
    assert "orlib_spp_results.csv" in digest and "timefold" in digest
    assert "benchmark_results.csv" in used


def test_build_solver_skill_saves_new_version(skill_dir, monkeypatch):
    monkeypatch.setattr(solver_advisor, "gather_evidence",
                        lambda: ("DIGEST", ["orlib_spp_results.csv"]))
    doc = "# Обновлённая база знаний\n" + ("детали. " * 40)
    monkeypatch.setattr(llm_backend, "complete",
                        lambda *a, **k: {"ok": True, "text": doc,
                                         "backend": "local", "model": "m"})
    res = solver_advisor.build_solver_skill(backend="local",
                                            now="2026-07-14T12:00:00")
    assert res["ok"] and res["meta"]["version"] == 1
    assert res["meta"]["source"] == "llm"
    assert res["evidence"] == ["orlib_spp_results.csv"]
    assert solver_advisor.load_skill()["text"].startswith("# Обновлённая")


def test_build_solver_skill_rejects_short_output(skill_dir, monkeypatch):
    monkeypatch.setattr(solver_advisor, "gather_evidence",
                        lambda: ("DIGEST", []))
    monkeypatch.setattr(llm_backend, "complete",
                        lambda *a, **k: {"ok": True, "text": "too short",
                                         "backend": "local", "model": "m"})
    res = solver_advisor.build_solver_skill(backend="local")
    assert res["ok"] is False and "коротк" in res["error"]


def test_build_solver_skill_handles_llm_failure(skill_dir, monkeypatch):
    monkeypatch.setattr(solver_advisor, "gather_evidence",
                        lambda: ("DIGEST", []))
    monkeypatch.setattr(llm_backend, "complete",
                        lambda *a, **k: {"ok": False, "error": "down",
                                         "backend": "cloud", "model": "m"})
    res = solver_advisor.build_solver_skill(backend="local")
    assert res["ok"] is False and res["error"] == "down"


def test_start_skill_build_busy_guard(monkeypatch):
    monkeypatch.setattr(solver_advisor, "_build_state",
                        {"running": True, "started_at": "x",
                         "finished_at": None, "ok": None, "error": None,
                         "version": None, "backend": "cloud"})
    out = solver_advisor.start_skill_build("cloud")
    assert out["already_running"] is True


# ---------------- оценка эффективности публикаций (impact) ----------------

def test_parse_impact_normalizes_and_clamps():
    from webapp import solver_advisor as sa
    got = sa.parse_impact('мусор {"score": 250, "confidence": "HIGH", '
                          '"rationale": "x"} хвост')
    assert got == {"score": 100, "confidence": "high", "rationale": "x",
                   "published": None}
    assert sa.parse_impact("нет json") is None
    assert sa.parse_impact('{"score": "abc"}') is None


def test_parse_impact_reads_published():
    from webapp import solver_advisor as sa
    got = sa.parse_impact('{"score": 40, "confidence": "med", '
                          '"rationale": "r", "published": "2025-05"}')
    assert got["published"] == "2025-05"
    # выдуманные/кривые даты не проходят нормализацию
    bad = sa.parse_impact('{"score": 40, "published": "05/2025"}')
    assert bad["published"] is None


def test_normalize_published_validates_ranges():
    from webapp import solver_advisor as sa
    assert sa.normalize_published("2025-05-13") == "2025-05-13"
    assert sa.normalize_published("2025-05") == "2025-05"
    assert sa.normalize_published("2025") == "2025"
    for bad in ("2025-13", "2025-00", "2025-01-32", "1901-05",
                "3000-01", "мусор", 2025, None, ""):
        assert sa.normalize_published(bad) is None


def test_published_from_url_arxiv_ids():
    from webapp import solver_advisor as sa
    assert sa.published_from_url(
        "https://arxiv.org/abs/2505.01234") == "2025-05"
    assert sa.published_from_url(
        "https://arxiv.org/pdf/2311.98765v2") == "2023-11"
    assert sa.published_from_url(
        "https://arxiv.org/abs/math/0601001") == "2006-01"
    assert sa.published_from_url(
        "https://arxiv.org/abs/hep-th/9901001") == "1999-01"
    assert sa.published_from_url("https://example.org/paper") is None
    assert sa.published_from_url(None) is None
    # месяц 13 в новом формате — не дата
    assert sa.published_from_url(
        "https://arxiv.org/abs/2513.01234") is None


def test_impact_cache_key_tracks_content_and_skill():
    from webapp import solver_advisor as sa
    f1 = {"title": "A", "summary": "S"}
    k1 = sa.impact_cache_key(f1, "v1")
    assert k1 == sa.impact_cache_key(dict(f1), "v1")
    assert k1 != sa.impact_cache_key({"title": "A", "summary": "S2"}, "v1")
    assert k1 != sa.impact_cache_key(f1, "v2")


def test_score_finding_degrades_to_unscored_without_llm(monkeypatch):
    """Недоступность модели — честный источник unscored, не исключение
    и не выдуманное число."""
    from webapp import solver_advisor as sa

    def boom(*a, **k):
        raise RuntimeError("no backend in tests")

    monkeypatch.setattr(sa.llm_backend, "complete", boom)
    got = sa.score_finding({"id": 1, "title": "T", "summary": "S"})
    assert got["source"] == "unscored"
    assert "score" not in got
    assert got["cache_key"]


def test_score_finding_reads_real_complete_contract(monkeypatch):
    """complete() возвращает СЛОВАРЬ {ok, text, model, error} — оценка
    обязана читать text, а не скармливать словарь парсеру (регресс:
    90/90 «ответ модели не разобран» на Test)."""
    from webapp import solver_advisor as sa

    def fake_ok(prompt, **kw):
        assert kw.get("usage") == "advisor"
        return {"ok": True, "backend": "cloud", "model": "m-test",
                "error": None,
                "text": 'до {"score": 42, "confidence": "med", '
                        '"rationale": "r", "published": "2025-05"} после'}

    monkeypatch.setattr(sa.llm_backend, "complete", fake_ok)
    got = sa.score_finding({"id": 1, "title": "T", "summary": "S"})
    assert got["source"] == "llm"
    assert got["score"] == 42
    assert got["model"] == "m-test"
    assert got["published"] == "2025-05"


def test_score_finding_surfaces_backend_error(monkeypatch):
    """ok=False от complete() — это unscored с ПРИЧИНОЙ из error, а не
    немое «не разобран»."""
    from webapp import solver_advisor as sa

    def fake_fail(prompt, **kw):
        return {"ok": False, "backend": "cloud", "model": "claude-cli",
                "error": "cloud CLI spawn failed: X", "text": ""}

    monkeypatch.setattr(sa.llm_backend, "complete", fake_fail)
    got = sa.score_finding({"id": 1, "title": "T", "summary": "S"})
    assert got["source"] == "unscored"
    assert "score" not in got
    assert "cloud CLI spawn failed" in got["error"]
