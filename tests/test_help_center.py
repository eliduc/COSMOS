"""Help center (Помощь): Ask AI routing per the L/C contour attribute,
literal-mention auto-detect, guardrail prompt plumbing, daily limits, and
the suggestions store (tenancy: own vs admin)."""
import importlib

import pytest
from fastapi.testclient import TestClient

from webapp import help_center as hc
from _stand_identity import ADMIN_EMAIL, ADMIN_SLUG

ADMIN = ADMIN_EMAIL
OTHER = "someone.else@example.com"


def _vis(*insts):
    """Minimal visible-instance dicts."""
    out = []
    for iid, scope in insts:
        out.append({"id": iid, "label": iid.replace("gerad-", "inst"),
                    "source": "gerad", "llm_scope": scope,
                    "legs": 100, "employees": 33,
                    "catalog_objective": None, "stage2_available": False})
    return out


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(hc, "SUGGESTIONS_PATH", tmp_path / "sug.json")
    monkeypatch.setattr(hc, "ASK_STATS_PATH", tmp_path / "ask_stats.json")
    yield tmp_path


@pytest.fixture()
def fake_llm(monkeypatch):
    """Capture complete() calls; echo the backend it was asked to use."""
    calls = []

    def fake_complete(prompt, *, backend, timeout_s=0, system=None, cwd=None,
                      usage=None, model=None):
        calls.append({"prompt": prompt, "backend": backend,
                      "system": system})
        return {"ok": True, "text": f"answered via {backend}",
                "backend": backend, "model": "fake", "error": None}

    monkeypatch.setattr(hc.llm_backend, "complete", fake_complete)
    return calls


# ----------------------------------------------------------------- routing

def test_route_local_when_any_dataset_is_L():
    vis = _vis(("gerad-1", "any"), ("upload-x", "local-only"))
    scope, backend = hc.route_backend(vis)
    assert scope == "local-only" and backend == "local"


def test_route_default_when_all_C(monkeypatch):
    monkeypatch.setenv("CREW_LLM_DEFAULT_BACKEND", "cloud")
    vis = _vis(("gerad-1", "any"), ("gerad-2", "any"))
    scope, backend = hc.route_backend(vis)
    assert scope == "any" and backend == "cloud"


def test_route_no_datasets_uses_default(monkeypatch):
    monkeypatch.setenv("CREW_LLM_DEFAULT_BACKEND", "local")
    scope, backend = hc.route_backend([])
    assert scope == "any" and backend == "local"


def test_detect_instances_by_literal_mention():
    fleet = f"upload-{ADMIN_SLUG}-fleet"
    vis = _vis(("gerad-1", "any"), ("gerad-2", "any"),
               (fleet, "local-only"))
    q = f"Сколько экипажа в gerad-1 и в наборе {fleet.upper()}?"
    assert set(hc.detect_instances(q, vis)) == {"gerad-1", fleet}
    # label match too (inst2), separator-insensitive
    assert hc.detect_instances("что в inst2?", vis) == ["gerad-2"]
    # no 1-3 char false positives
    assert hc.detect_instances("объясни KPI", vis) == []


def test_detect_respects_token_boundaries():
    """'gerad-10' must NOT latch onto 'gerad-1' (audit 2026-07-15)."""
    vis = _vis(("gerad-1", "any"))
    assert hc.detect_instances("что в наборе gerad-10?", vis) == []
    assert hc.detect_instances("что в наборе gerad-1?", vis) == ["gerad-1"]


def test_detect_matches_cyrillic_mention_of_transliterated_label():
    """Upload labels are transliterated Russian names — the user typing the
    ORIGINAL Russian must still hit the auto-detect safety net (audit
    2026-07-15)."""
    inst_id = f"upload-{ADMIN_SLUG}-aeroflot"
    vis = [{"id": inst_id, "label": f"{ADMIN_SLUG}-aeroflot",
            "source": "upload", "llm_scope": "local-only",
            "legs": 10, "employees": 5,
            "catalog_objective": None, "stage2_available": False}]
    got = hc.detect_instances("сколько экипажа в наборе Аэрофлот?", vis)
    assert got == [inst_id]


def test_route_fails_closed_on_unknown_scope():
    """A corrupted/missing llm_scope counts as local-only, never as cloud
    permission (audit 2026-07-15)."""
    weird = [{"id": "x", "llm_scope": "banana"}, {"id": "y"}]
    scope, backend = hc.route_backend(weird)
    assert scope == "local-only" and backend == "local"


def test_ask_merges_selection_and_detection(iso, fake_llm, monkeypatch):
    monkeypatch.setenv("CREW_LLM_DEFAULT_BACKEND", "cloud")
    vis = _vis(("gerad-1", "any"), ("upload-x", "local-only"))
    user = {"email": ADMIN}
    # explicit C-instance + question mentions the L-instance -> local wins
    res = hc.ask("сравни gerad-1 с upload-x", ["gerad-1"], user, vis)
    assert res["ok"] is True
    assert set(res["instances"]) == {"gerad-1", "upload-x"}
    assert res["scope"] == "local-only" and res["backend"] == "local"
    assert fake_llm[-1]["backend"] == "local"


def test_ask_without_data_uses_default(iso, fake_llm, monkeypatch):
    monkeypatch.setenv("CREW_LLM_DEFAULT_BACKEND", "cloud")
    res = hc.ask("как запустить этап 2?", [], {"email": ADMIN}, _vis())
    assert res["backend"] == "cloud" and res["instances"] == []


def test_ask_rejects_invisible_instance(iso, fake_llm):
    vis = _vis(("gerad-1", "any"))
    with pytest.raises(hc.HelpCenterError):
        hc.ask("вопрос", ["upload-foreign"], {"email": ADMIN}, vis)


def test_ask_validates_question(iso, fake_llm):
    with pytest.raises(hc.HelpCenterError):
        hc.ask("", [], {"email": ADMIN}, _vis())
    with pytest.raises(hc.HelpCenterError):
        hc.ask("x" * (hc.QUESTION_MAX + 1), [], {"email": ADMIN}, _vis())


def test_ask_prompt_carries_guardrails_and_context(iso, fake_llm):
    vis = _vis(("gerad-1", "any"))
    hc.ask("что такое пейринг в gerad-1?", [], {"email": ADMIN}, vis)
    call = fake_llm[-1]
    assert "STRICT SCOPE" in call["system"]
    assert "APPLICATION OVERVIEW" in call["prompt"]
    assert "gerad-1" in call["prompt"]          # dataset context included
    assert "<<<" in call["prompt"]              # question delimited as data


def test_ask_daily_limit(iso, fake_llm, monkeypatch):
    monkeypatch.setattr(hc, "ASK_DAILY_LIMIT", 3)
    for _ in range(3):
        hc.ask("вопрос о программе", [], {"email": ADMIN}, _vis())
    with pytest.raises(hc.HelpCenterError):
        hc.ask("ещё вопрос", [], {"email": ADMIN}, _vis())
    # another user is not affected
    hc.ask("вопрос", [], {"email": OTHER}, _vis())


@pytest.mark.parametrize("raw,expect_refused,expect_answer", [
    ("OFF_TOPIC: Я отвечаю только о COSMOS.", True,
     "Я отвечаю только о COSMOS."),
    ("**OFF_TOPIC:** Off limits.", True, "Off limits."),
    ("[off topic] — вне темы.", True, "вне темы."),
    ("Этап 2 запускается во вкладке «Расчёты».", False,
     "Этап 2 запускается во вкладке «Расчёты»."),
    ("Тема OFF_TOPIC обсуждается в середине — не отказ.", False,
     "Тема OFF_TOPIC обсуждается в середине — не отказ."),
])
def test_off_topic_marker_is_stripped_into_flag(iso, monkeypatch, raw,
                                                expect_refused,
                                                expect_answer):
    """The model marks refusals with OFF_TOPIC: — the backend strips the
    marker (with tolerated decoration) into a `refused` flag so the UI can
    render the refusal red (user request 2026-07-15)."""
    def fake(prompt, *, backend, timeout_s=0, system=None, cwd=None, usage=None, model=None):
        assert "OFF_TOPIC" in system          # instruction present
        return {"ok": True, "text": raw, "backend": backend,
                "model": "fake", "error": None}
    monkeypatch.setattr(hc.llm_backend, "complete", fake)
    res = hc.ask("вопрос", [], {"email": ADMIN}, _vis())
    assert res["refused"] is expect_refused
    assert res["answer"] == expect_answer


def test_prefer_cloud_used_when_all_C(iso, fake_llm, monkeypatch):
    monkeypatch.setenv("CREW_LLM_DEFAULT_BACKEND", "local")
    vis = _vis(("gerad-1", "any"))
    res = hc.ask("вопрос про gerad-1", [], {"email": ADMIN}, vis,
                 prefer_cloud=True)
    assert res["backend"] == "cloud"          # preference overrides default


def test_prefer_cloud_refused_loudly_for_L(iso, fake_llm):
    vis = _vis(("upload-x", "local-only"))
    with pytest.raises(hc.HelpCenterError) as exc:
        hc.ask("вопрос", ["upload-x"], {"email": ADMIN}, vis,
               prefer_cloud=True)
    assert "upload-x" in str(exc.value)       # names the offending dataset
    # and the auto-detected L path is refused too, not silently downgraded
    vis2 = _vis(("gerad-1", "any"), ("upload-x", "local-only"))
    with pytest.raises(hc.HelpCenterError):
        hc.ask("сравни gerad-1 и upload-x", [], {"email": ADMIN}, vis2,
               prefer_cloud=True)


def test_ask_llm_failure_is_reported_not_raised(iso, monkeypatch):
    def broken(prompt, *, backend, timeout_s=0, system=None, cwd=None, usage=None, model=None):
        return {"ok": False, "text": "", "backend": backend,
                "model": "fake", "error": "unreachable"}
    monkeypatch.setattr(hc.llm_backend, "complete", broken)
    res = hc.ask("вопрос", [], {"email": ADMIN}, _vis())
    assert res["ok"] is False and res["error"] == "unreachable"


# ------------------------------------------------------------- suggestions

def test_suggestion_roundtrip_and_tenancy(iso):
    rec = hc.add_suggestion({"email": ADMIN}, "Добавьте тёмную тему",
                            "interface")
    assert rec["id"].startswith("s") and rec["status"] == "new"
    assert rec["at"].endswith("+00:00")
    hc.add_suggestion({"email": OTHER}, "Ускорьте этап 1", "algorithms")
    # user sees own only
    mine = hc.list_suggestions({"email": OTHER}, is_admin=False)
    assert [r["text"] for r in mine] == ["Ускорьте этап 1"]
    # admin sees all, newest first
    both = hc.list_suggestions({"email": ADMIN}, is_admin=True)
    assert len(both) == 2


def test_suggestion_validation(iso):
    with pytest.raises(hc.HelpCenterError):
        hc.add_suggestion({"email": ADMIN}, "ok", "interface")   # too short
    with pytest.raises(hc.HelpCenterError):
        hc.add_suggestion({"email": ADMIN}, "x" * 5000, "interface")
    with pytest.raises(hc.HelpCenterError):
        hc.add_suggestion({"email": ADMIN}, "нормальный текст", "spam")


def test_suggestion_delete_rights(iso):
    rec = hc.add_suggestion({"email": OTHER}, "Идея про интерфейс",
                            "interface")
    # a third user cannot delete someone else's entry
    assert hc.delete_suggestion(rec["id"], {"email": "x@y.zz"},
                                is_admin=False) is False
    # the author can
    assert hc.delete_suggestion(rec["id"], {"email": OTHER},
                                is_admin=False) is True
    # admin can delete anything
    rec2 = hc.add_suggestion({"email": OTHER}, "Ещё одна идея", "other")
    assert hc.delete_suggestion(rec2["id"], {"email": ADMIN},
                                is_admin=True) is True


def test_suggestion_daily_limit(iso, monkeypatch):
    monkeypatch.setattr(hc, "SUGGESTIONS_DAILY_LIMIT", 2)
    hc.add_suggestion({"email": ADMIN}, "Первая идея тут", "other")
    hc.add_suggestion({"email": ADMIN}, "Вторая идея тут", "other")
    with pytest.raises(hc.HelpCenterError):
        hc.add_suggestion({"email": ADMIN}, "Третья идея тут", "other")


# --------------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN)
    monkeypatch.setenv("CREW_LLM_DEFAULT_BACKEND", "cloud")
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(hc, "SUGGESTIONS_PATH", tmp_path / "sug.json")
    monkeypatch.setattr(hc, "ASK_STATS_PATH", tmp_path / "ask_stats.json")

    def fake_complete(prompt, *, backend, timeout_s=0, system=None, cwd=None,
                      usage=None, model=None):
        return {"ok": True, "text": f"via {backend}", "backend": backend,
                "model": "fake", "error": None}
    monkeypatch.setattr(hc.llm_backend, "complete", fake_complete)
    users.ensure_db()
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def test_api_ask_routes_by_scope(client):
    # seed instances are 'any' by default -> install default (cloud here)
    r = client.post("/api/ask", json={"question": "что такое этап 1?",
                                      "instances": []})
    assert r.status_code == 200, r.text
    assert r.json()["backend"] == "cloud"
    # explicit seed instance stays 'any'
    r2 = client.post("/api/ask", json={"question": "экипаж в gerad-1?",
                                       "instances": ["gerad-1"]})
    assert r2.json()["instances"] == ["gerad-1"]


def test_api_ask_rejects_bad_input(client):
    assert client.post("/api/ask",
                       json={"question": ""}).status_code == 422
    # invisible/nonexistent instance -> 404, same discipline as every other
    # endpoint (audit 2026-07-15)
    assert client.post("/api/ask",
                       json={"question": "q",
                             "instances": ["upload-nope"]}).status_code == 404


def test_api_suggestions_flow(client):
    r = client.post("/api/suggestions",
                    json={"text": "Сделайте экспорт в Excel",
                          "category": "interface"})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    lst = client.get("/api/suggestions").json()
    assert lst["is_admin"] is True
    assert any(x["id"] == sid for x in lst["items"])
    assert client.delete(f"/api/suggestions/{sid}").status_code == 200
    assert client.delete(f"/api/suggestions/{sid}").status_code == 404


def test_api_ask_backends_probe(client, monkeypatch):
    monkeypatch.setattr(hc.llm_backend, "available",
                        lambda: {"local": False, "cloud": True})
    from webapp import llm_backend as lb
    monkeypatch.setattr(lb, "available",
                        lambda: {"local": False, "cloud": True})
    r = client.get("/api/ask/backends")
    assert r.status_code == 200
    assert set(r.json()) == {"local", "cloud"}
