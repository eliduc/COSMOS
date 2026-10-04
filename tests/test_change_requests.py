"""CR pipeline (Автоулучшение): feedback clustering, regulatory/uiux
agents, suggestion status transitions, admin actions, implementer in an
isolated tmp git repo, daily quota, API gating."""
import importlib
import json
import re
import subprocess

import pytest
from fastapi.testclient import TestClient

from webapp import change_requests as crq
from webapp import help_center as hc
from _stand_identity import ADMIN_EMAIL

ADMIN = ADMIN_EMAIL
USER = "someone@example.com"


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(crq, "CR_PATH", tmp_path / "crs.json")
    monkeypatch.setattr(hc, "SUGGESTIONS_PATH", tmp_path / "sug.json")
    monkeypatch.setattr(hc, "ASK_STATS_PATH", tmp_path / "ask.json")
    yield tmp_path


def _seed_suggestions():
    a = hc.add_suggestion({"email": USER}, "Хочу экспорт ростера в Excel",
                          "interface")
    b = hc.add_suggestion({"email": ADMIN}, "Дайте выгрузку в Excel-файл",
                          "interface")
    c = hc.add_suggestion({"email": USER}, "Ускорьте этап 1", "algorithms")
    return a, b, c


def _fake_llm(reply):
    def fake(prompt, *, backend, timeout_s=0, system=None, cwd=None,
             usage=None, model=None):
        return {"ok": True, "text": reply, "backend": backend,
                "model": "fake", "error": None}
    return fake


# ------------------------------------------------------------ feedback agent

def test_feedback_agent_clusters_and_links(iso, monkeypatch):
    a, b, c = _seed_suggestions()
    reply = json.dumps([
        {"title": "Экспорт в Excel", "summary": "Сделать выгрузку",
         "suggestion_ids": [a["id"], b["id"]], "effort": 4},
        {"title": "Ускорение этапа 1", "summary": "Оптимизировать",
         "suggestion_ids": [c["id"]], "effort": 8},
    ])
    monkeypatch.setattr(crq.llm_backend, "complete", _fake_llm(reply))
    out = crq.run_feedback_agent()
    assert out["created"] == 2 and out["considered"] == 3
    crs = crq.list_crs()
    assert len(crs) == 2
    excel = next(r for r in crs if "Excel" in r["title"])
    assert excel["priority"] == 2 and excel["effort"] == 4
    assert excel["status"] == "draft"
    # suggestion statuses moved to cr_created with the link
    items = hc.list_suggestions({"email": ""}, is_admin=True)
    by_id = {s["id"]: s for s in items}
    assert by_id[a["id"]]["status"] == "cr_created"
    assert by_id[a["id"]]["cr_id"] == excel["id"]


def test_feedback_agent_ignores_unknown_and_duplicate_ids(iso, monkeypatch):
    a, b, c = _seed_suggestions()
    reply = json.dumps([
        {"title": "X", "summary": "s",
         "suggestion_ids": [a["id"], "sFAKE", a["id"]], "effort": 99},
        {"title": "Y", "summary": "s", "suggestion_ids": [a["id"]]},
    ])
    monkeypatch.setattr(crq.llm_backend, "complete", _fake_llm(reply))
    out = crq.run_feedback_agent()
    assert out["created"] == 1                     # second cluster empty
    cr = crq.list_crs()[0]
    assert cr["suggestion_ids"] == [a["id"]]       # dedup + unknown dropped
    assert cr["effort"] == 10                      # clamped
    # untouched suggestions were triaged
    items = {s["id"]: s for s in
             hc.list_suggestions({"email": ""}, is_admin=True)}
    assert items[b["id"]]["status"] == "triaged"
    assert items[c["id"]]["status"] == "triaged"


def test_feedback_agent_no_fresh(iso, monkeypatch):
    out = crq.run_feedback_agent()
    assert out["created"] == 0


def test_agent_llm_failure_is_loud(iso, monkeypatch):
    _seed_suggestions()
    def broken(prompt, *, backend, timeout_s=0, system=None, cwd=None,
               usage=None, model=None):
        return {"ok": False, "text": "", "backend": backend,
                "model": "x", "error": "down"}
    monkeypatch.setattr(crq.llm_backend, "complete", broken)
    with pytest.raises(crq.ChangeRequestError):
        crq.run_feedback_agent()


# ------------------------------------------------- regulatory / uiux agents

def test_regulatory_agent_uses_countries(iso, monkeypatch):
    monkeypatch.setattr(crq, "instance_countries", lambda: ["INTL", "US"])
    reply = json.dumps([{"title": "FAR 117 поправка",
                         "summary": "Изменение лимитов", "effort": 6,
                         "source": "FAA FAR 117"}])
    monkeypatch.setattr(crq.llm_backend, "complete", _fake_llm(reply))
    out = crq.run_regulatory_agent()
    assert out["created"] == 2                      # one per country
    crs = crq.list_crs()
    assert {r["country"] for r in crs} == {"INTL", "US"}
    assert all(r["agent"] == "regulatory" for r in crs)


def test_uiux_agent(iso, monkeypatch):
    reply = json.dumps([{"title": "Хлебные крошки", "summary": "Добавить",
                         "effort": 3, "source": "эвристика: visibility"}])
    monkeypatch.setattr(crq.llm_backend, "complete", _fake_llm(reply))
    out = crq.run_uiux_agent()
    assert out["created"] == 1
    assert crq.list_crs()[0]["agent"] == "uiux"


# ------------------------------------------------------------ admin actions

def test_accept_reject_transitions(iso, monkeypatch):
    a, b, _ = _seed_suggestions()
    reply = json.dumps([{"title": "T", "summary": "s",
                         "suggestion_ids": [a["id"], b["id"]],
                         "effort": 3}])
    monkeypatch.setattr(crq.llm_backend, "complete", _fake_llm(reply))
    crq.run_feedback_agent()
    cr = crq.list_crs()[0]
    out = crq.accept_cr(cr["id"], ADMIN)
    assert out["status"] == "accepted"
    items = {s["id"]: s for s in
             hc.list_suggestions({"email": ""}, is_admin=True)}
    assert items[a["id"]]["status"] == "cr_accepted"
    # cannot re-accept
    with pytest.raises(crq.ChangeRequestError):
        crq.accept_cr(cr["id"], ADMIN)
    out2 = crq.reject_cr(cr["id"], ADMIN, "не сейчас")
    assert out2["status"] == "rejected"
    assert out2["reject_reason"] == "не сейчас"
    items = {s["id"]: s for s in
             hc.list_suggestions({"email": ""}, is_admin=True)}
    assert items[a["id"]]["status"] == "triaged"    # released back


# --------------------------------------------------------------- implementer

@pytest.fixture()
def fake_repo(tmp_path, monkeypatch):
    """Tiny isolated git repo standing in for cosmos-test."""
    repo = tmp_path / "testrepo"
    repo.mkdir()
    def g(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True)
    g("init", "-b", "main")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (repo / "hello.py").write_text("x = 1\n", encoding="utf-8")
    g("add", "-A")
    g("commit", "-m", "init")
    from webapp import evolution_runner as ev
    monkeypatch.setenv("COSMOS_TEST_DIR", str(repo))
    # contour guard compares against prod/stage dirs; point them elsewhere
    monkeypatch.setenv("COSMOS_PROD_DIR", str(tmp_path / "prod"))
    monkeypatch.setenv("COSMOS_STAGE_DIR", str(tmp_path / "stage"))
    yield repo, ev


def _accepted_cr(monkeypatch):
    reply = json.dumps([{"title": "T", "summary": "поменять x",
                         "suggestion_ids": [], "effort": 1}])
    monkeypatch.setattr(crq.llm_backend, "complete", _fake_llm(reply))
    crq._add_crs([{
        "id": "crTEST01", "agent": "feedback", "title": "T",
        "summary": "поменять x на 2", "suggestion_ids": [],
        "priority": 1, "effort": 1, "status": "accepted",
        "source": None, "country": None,
        "created_at": crq._now(), "updated_at": crq._now()}])
    return "crTEST01"


def _run_mock(repo, ev, cli_effect, pytest_result):
    """ev._run stand-in: git commands run FOR REAL (ev._git goes through
    _run), the claude CLI applies cli_effect, pytest returns
    pytest_result."""
    def fake_run(cmd, cwd, timeout_s, env_extra=None, input_text=None,
                 on_line=None, scrub_prefixes=()):
        if cmd[0] == "git":
            p = subprocess.run(["git", "-C", str(cwd), *cmd[1:]],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        if cmd[0] == ev.claude_cli():
            return cli_effect()
        return pytest_result                        # the pytest call
    return fake_run


def test_implementer_happy_path(iso, fake_repo, monkeypatch):
    repo, ev = fake_repo
    cr_id = _accepted_cr(monkeypatch)

    def effect():
        (repo / "hello.py").write_text("x = 2\n", encoding="utf-8")
        return 0, "поменял x на 2"
    monkeypatch.setattr(ev, "_run",
                        _run_mock(repo, ev, effect, (0, "1 passed")))
    out = crq.implement_cr(cr_id, ADMIN)
    assert out["status"] == "implemented"
    assert out["impl"]["tests_ok"] is True
    assert out["impl"]["branch"] == "cr-crTEST01"
    assert "hello.py" in out["impl"]["diff_stat"]
    # repo back on main, branch exists with the commit
    head = subprocess.run(["git", "-C", str(repo), "branch",
                           "--show-current"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout.strip()
    assert head == "main"


def test_implementer_uses_broker_when_available(iso, fake_repo, monkeypatch):
    """Аудит R10 (P0-04): второй контур исполнения (CR) шёл всегда прямым
    subprocess под записью приложения. Готовый посредник — единственный
    маршрут; способ запуска записывается в саму запись."""
    repo, ev = fake_repo
    cr_id = _accepted_cr(monkeypatch)
    from webapp import agent_broker as ab

    monkeypatch.setattr(ab, "available", lambda: True)

    def fake_launch(payload, *, run_id, start_timeout_s=120,
                    exec_script=None):
        assert run_id == f"cr-{cr_id}"
        assert exec_script is None          # агент, не exec-режим
        (repo / "hello.py").write_text("x = 3\n", encoding="utf-8")
        return {"pid": 1, "owner_sid": "S-1-5-21-agent",
                "handoff": str(repo)}

    monkeypatch.setattr(ab, "launch", fake_launch)
    monkeypatch.setattr(ev, "_broker_wait",
                        lambda hand, timeout_s: (0, "готово", ""))
    monkeypatch.setattr(ev, "_run", _run_mock(
        repo, ev,
        lambda: (1, "CLI не должен вызываться при готовом посреднике"),
        (0, "1 passed")))
    out = crq.implement_cr(cr_id, ADMIN)
    assert out["status"] == "implemented"
    assert out["impl"]["isolation"] == "broker"


def test_implementer_needs_human_marker(iso, fake_repo, monkeypatch):
    repo, ev = fake_repo
    cr_id = _accepted_cr(monkeypatch)
    monkeypatch.setattr(ev, "_run", _run_mock(
        repo, ev,
        lambda: (0, "нужна новая зависимость. NEEDS_HUMAN: deps"),
        (0, "")))
    out = crq.implement_cr(cr_id, ADMIN)
    assert out["status"] == "needs_human"
    # tree untouched
    assert (repo / "hello.py").read_text(encoding="utf-8") == "x = 1\n"


def test_implementer_failing_tests_needs_human(iso, fake_repo, monkeypatch):
    repo, ev = fake_repo
    cr_id = _accepted_cr(monkeypatch)

    def effect():
        (repo / "hello.py").write_text("x = broken(\n", encoding="utf-8")
        return 0, "done"
    monkeypatch.setattr(ev, "_run",
                        _run_mock(repo, ev, effect, (1, "1 failed")))
    out = crq.implement_cr(cr_id, ADMIN)
    assert out["status"] == "needs_human"
    assert out["impl"]["tests_ok"] is False


def test_implementer_daily_quota(iso, fake_repo, monkeypatch):
    repo, ev = fake_repo
    monkeypatch.setattr(crq, "CR_IMPL_DAILY_LIMIT", 1)
    cr_id = _accepted_cr(monkeypatch)

    def effect():
        (repo / "hello.py").write_text("x = 2\n", encoding="utf-8")
        return 0, "ok"
    monkeypatch.setattr(ev, "_run",
                        _run_mock(repo, ev, effect, (0, "1 passed")))
    crq.implement_cr(cr_id, ADMIN)
    crq._add_crs([{**crq.get_cr(cr_id), "id": "crTEST02",
                   "status": "accepted"}])
    with pytest.raises(crq.ChangeRequestError):
        crq.implement_cr("crTEST02", ADMIN)


def test_implementer_failed_cli_cleans_tree_and_refunds_quota(
        iso, fake_repo, monkeypatch):
    """Audit HIGH-2/MEDIUM-4: a CLI failure must not strand the checkout
    on a dirty cr-branch (bricking every next run) nor burn the quota."""
    repo, ev = fake_repo
    cr_id = _accepted_cr(monkeypatch)

    def effect():                                    # writes junk, then dies
        (repo / "junk.py").write_text("boom\n", encoding="utf-8")
        return 2, "crashed"
    monkeypatch.setattr(ev, "_run",
                        _run_mock(repo, ev, effect, (0, "")))
    with pytest.raises(crq.ChangeRequestError):
        crq.implement_cr(cr_id, ADMIN)
    # checkout is clean, on main, work branch gone
    st = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
    br = subprocess.run(["git", "-C", str(repo), "branch",
                         "--show-current"], capture_output=True,
                        text=True, encoding="utf-8", errors="replace").stdout.strip()
    branches = subprocess.run(["git", "-C", str(repo), "branch"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    assert st == "" and br == "main" and "cr-" not in branches
    # CR honestly rolled back, quota refunded, retry possible
    assert crq.get_cr(cr_id)["status"] == "accepted"
    assert crq._impl_quota_left() == crq.CR_IMPL_DAILY_LIMIT

    def good():
        (repo / "hello.py").write_text("x = 2\n", encoding="utf-8")
        return 0, "ok"
    monkeypatch.setattr(ev, "_run",
                        _run_mock(repo, ev, good, (0, "1 passed")))
    assert crq.implement_cr(cr_id, ADMIN)["status"] == "implemented"


def test_implementer_diff_guard_blocks_forbidden_paths(
        iso, fake_repo, monkeypatch):
    """Audit MEDIUM-3: HARD RULES are enforced on the actual diff, not
    just requested in the prompt."""
    repo, ev = fake_repo
    cr_id = _accepted_cr(monkeypatch)

    def effect():                                    # model misbehaves
        (repo / "webapp" / "config").mkdir(parents=True)
        (repo / "webapp" / "config" / "evil.json").write_text(
            "{}", encoding="utf-8")
        (repo / "requirements-extra.txt").write_text(
            "evilpkg\n", encoding="utf-8")
        (repo / "hello.py").write_text("x = 2\n", encoding="utf-8")
        return 0, "готово"
    monkeypatch.setattr(ev, "_run",
                        _run_mock(repo, ev, effect, (0, "1 passed")))
    out = crq.implement_cr(cr_id, ADMIN)
    assert out["status"] == "needs_human"
    assert "diff guard" in out["impl"]["report"]
    assert "webapp/config/evil.json" in out["impl"]["report"]
    # nothing committed, tree clean on main
    st = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
    branches = subprocess.run(["git", "-C", str(repo), "branch"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    assert st == "" and "cr-" not in branches
    assert (repo / "hello.py").read_text(encoding="utf-8") == "x = 1\n"


def test_forbidden_paths_denylist():
    bad = crq._forbidden_paths([
        "requirements-dev.txt", ".env.local", "deploy_step1.ps1",
        "data/gerad/x.json", ".github/workflows/ci.yml", "pom.xml",
        "webapp\\config\\llm_scope.json",             # windows separators
        "webapp/app.py", "tests/test_x.py", "webapp/static/app.js"])
    assert "webapp/app.py" not in bad
    assert "tests/test_x.py" not in bad
    assert "webapp/static/app.js" not in bad
    assert len(bad) == 7


def test_implementer_refuses_when_pipeline_busy(iso, fake_repo, monkeypatch):
    """Audit HIGH-1: the evolution pipeline and the implementer share the
    test checkout — two writers must never run concurrently."""
    repo, ev = fake_repo
    cr_id = _accepted_cr(monkeypatch)
    assert ev._pipeline_lock.acquire(blocking=False)
    try:
        with pytest.raises(crq.ChangeRequestError, match="busy"):
            crq.implement_cr(cr_id, ADMIN)
    finally:
        ev._pipeline_lock.release()
    assert crq.get_cr(cr_id)["status"] == "accepted"  # untouched
    assert crq._impl_quota_left() == crq.CR_IMPL_DAILY_LIMIT


def test_recover_interrupted_unsticks_implementing(iso, solver_stub):
    """Audit LOW-6: a hard crash mid-run leaves 'implementing'; the boot
    sweep flips it to needs_human so accept->implement can re-drive it.

    `solver_stub` обязателен: подметание зовёт сборщика сирот СВОЕГО
    контура, и без подставного модуля тест снимал бы НАСТОЯЩИЕ эфемерные
    решатели машины — 09.09.2026 так погиб судья сквозной проверки посреди
    полного набора клона."""
    crq._add_crs([{"id": "crSTUCK", "agent": "uiux", "title": "t",
                   "summary": "s", "suggestion_ids": [], "priority": 1,
                   "effort": 1, "status": "implementing", "source": None,
                   "country": None, "created_at": crq._now(),
                   "updated_at": crq._now()}])
    crq.recover_interrupted()
    cr = crq.get_cr("crSTUCK")
    assert cr["status"] == "needs_human"
    assert cr["impl"]["needs_human"] is True
    assert crq.accept_cr("crSTUCK", ADMIN)["status"] == "accepted"


def test_implementer_refuses_on_internal_contour(iso, fake_repo, monkeypatch):
    """Строгий режим (§8-бис документа формализации): Реализатор кладёт в
    ОБЛАЧНЫЙ промпт тексты предложений пользователей — на чекауте
    prod/stage он обязан громко отказаться, не тронув CR и квоту."""
    repo, ev = fake_repo
    cr_id = _accepted_cr(monkeypatch)
    from webapp import registry
    monkeypatch.setenv("COSMOS_PROD_DIR", str(registry.REPO_ROOT))
    with pytest.raises(crq.ChangeRequestError, match="внутренний контур"):
        crq.implement_cr(cr_id, ADMIN)
    assert crq.get_cr(cr_id)["status"] == "accepted"     # untouched
    assert crq._impl_quota_left() == crq.CR_IMPL_DAILY_LIMIT


def test_implementer_fail_closed_when_contour_unresolvable(
        iso, fake_repo, monkeypatch):
    """Unresolvable contour dirs count as internal (fail-closed): a broken
    env must not open the cloud egress path."""
    repo, ev = fake_repo
    cr_id = _accepted_cr(monkeypatch)
    def boom():
        raise RuntimeError("env broken")
    monkeypatch.setattr(ev, "prod_dir", boom)
    with pytest.raises(crq.ChangeRequestError, match="контур"):
        crq.implement_cr(cr_id, ADMIN)


def test_agent_backend_fail_closed_when_contour_unresolvable(monkeypatch):
    """_agent_backend used to silently keep the cloud backend when the
    contour dirs failed to resolve — now it degrades to local."""
    monkeypatch.setenv("CREW_LLM_DEFAULT_BACKEND", "cloud")
    from webapp import evolution_runner as ev
    def boom():
        raise RuntimeError("env broken")
    monkeypatch.setattr(ev, "prod_dir", boom)
    assert crq._agent_backend() == "local"


def test_implementer_requires_accepted_status(iso, fake_repo, monkeypatch):
    crq._add_crs([{"id": "crX", "agent": "uiux", "title": "t",
                   "summary": "s", "suggestion_ids": [], "priority": 1,
                   "effort": 1, "status": "draft", "source": None,
                   "country": None, "created_at": crq._now(),
                   "updated_at": crq._now()}])
    with pytest.raises(crq.ChangeRequestError):
        crq.implement_cr("crX", ADMIN)


# --------------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN)
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(crq, "CR_PATH", tmp_path / "crs.json")
    monkeypatch.setattr(hc, "SUGGESTIONS_PATH", tmp_path / "sug.json")
    # suggest_stats.json derives from ASK_STATS_PATH (with_name) — isolate
    # it too, or repeated pytest runs exhaust the REAL 20/day limit
    monkeypatch.setattr(hc, "ASK_STATS_PATH", tmp_path / "ask_stats.json")
    users.ensure_db()
    users.add(USER, "user", by=ADMIN)
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def test_api_admin_only(client, monkeypatch):
    monkeypatch.setenv("CREW_DEV_USER", USER)
    assert client.get("/api/cr").status_code == 403
    assert client.post("/api/cr/agents/feedback/run").status_code == 403
    assert client.post("/api/cr/x/accept").status_code == 403


def test_api_agent_run_and_actions(client, monkeypatch):
    hc.add_suggestion({"email": USER}, "Экспорт ростера в Excel",
                      "interface")
    reply = json.dumps([{"title": "Экспорт", "summary": "s",
                         "suggestion_ids": [
                             hc.list_suggestions({"email": ""},
                                                 True)[0]["id"]],
                         "effort": 2}])
    monkeypatch.setattr(crq.llm_backend, "complete", _fake_llm(reply))
    r = client.post("/api/cr/agents/feedback/run")
    assert r.status_code == 200 and r.json()["created"] == 1
    lst = client.get("/api/cr").json()
    assert len(lst["items"]) == 1
    cr_id = lst["items"][0]["id"]
    assert client.post(f"/api/cr/{cr_id}/accept").status_code == 200
    r2 = client.post(f"/api/cr/{cr_id}/reject",
                     json={"reason": "потом"})
    assert r2.status_code == 200
    assert client.post("/api/cr/agents/nope/run").status_code == 404


# ------------------------------------- каталог шаблонов: строка CR (шаг 6)
#
# Приёмник внешних заявок (submit_external_crs) снят шагом 6: он клал в
# очередь машинную форму правила авиакомпании, а строку очереди читает
# облачная модель-исполнитель. Ниже — то, что пришло ему на смену.

def test_снятый_приёмник_внешних_заявок_не_воскрешён():
    """Сторож замены: `submit_external_crs` снят вместе со своим путём
    (решения 0.6/0.8), и вернуть его молча нельзя — строка очереди
    каталога несёт только обезличенную спецификацию записи."""
    assert not hasattr(crq, "submit_external_crs")
    from webapp import template_base as tb
    assert not hasattr(tb, "submit_change")


def _request(record_id="duty_limits", kinds=("check_code",),
             state="принята", section="families", draft=None,
             cr_key=None):
    """Запись заявки каталога (11 полей §6 схемы) с номером ПО ЗАКОНУ."""
    from template_catalog import requests as law
    if draft is None:
        key = law.kind_request_key(section, record_id, kinds)
    else:
        key = law.manual_key(section, record_id, law.draft_canon(draft))
    rid = law.request_id_of(key)
    return {
        "request_id": rid, "state": state, "section": section,
        "record_id": record_id,
        "submitted_at": "2026-09-12T10:15:00+03:00",
        "submitted_by": "COSMOS",
        "source": {"kind": "пробел при нажатии", "document_class": None,
                   "run_class": "прогон COSMOS",
                   "ref": "run-0031#" + key[:12]},
        "draft": draft,
        "executor_kinds_requested": (None if draft is not None
                                     else sorted(set(kinds))),
        "decision": ({"by": "владелец", "at": "2026-09-12T10:00:00+03:00",
                      "words": "механика нужна на разборе"}
                     if state in ("принята", "исполнитель заявлен",
                                  "отклонена", "есть")
                     else {"by": None, "at": None, "words": None}),
        "cr_key": cr_key,
    }


@pytest.fixture()
def catalog_copy(tmp_path, monkeypatch):
    """Копия каталога с разделом заявок; живой catalog/ не трогается."""
    import tests.catalog_copies as cc
    from vmp import catalog_access as ca

    def make(requests, at=None):
        path = cc.copy_catalog(at or tmp_path)
        (path / "requests.json").write_text(
            json.dumps({"form_version": 1, "requests": list(requests)},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        cc.use_catalog(monkeypatch, path)
        return path
    yield make
    ca.forget()


@pytest.fixture()
def external(tmp_path, monkeypatch):
    """Внешний контур: без него сверка строк не открывает (0.4)."""
    monkeypatch.setenv("COSMOS_PROD_DIR", str(tmp_path / "prod"))
    monkeypatch.setenv("COSMOS_STAGE_DIR", str(tmp_path / "stage"))


def _service(monkeypatch, mode="edit", fingerprint=None, calls=None):
    """Подставная служба каталога: режим, отпечаток и запись ключей."""
    from vmp import catalog_requests as vcr
    monkeypatch.setattr(
        vcr.service_client, "session",
        classmethod(lambda cls: (None if mode is None
                                 else {"mode": mode,
                                       "fingerprint": fingerprint})))

    def put(cls, request_id, cr_key, **kw):
        (calls if calls is not None else []).append((request_id, cr_key))
        return {"ok": True, "state": "исполнитель заявлен", "words": "ok"}
    monkeypatch.setattr(vcr.service_client, "put_cr_key",
                        classmethod(put))


def test_каталог_строка_рождается_принятой_без_правил(
        iso, catalog_copy, monkeypatch):
    """Форма строки §3.1: ключ по закону, статус accepted, в строке нет
    машинной формы правила и нет СЛОВ решения владельца — её читает
    облачная модель-исполнитель."""
    from template_catalog import requests as law
    from vmp import catalog_access as ca
    from webapp import catalog_pipeline as cp
    rq = _request()
    catalog_copy([rq])
    cat = ca.current()
    row = cp.build_row(rq, cat)
    assert row["id"] == law.cr_key_of(rq["request_id"])
    assert row["kind"] == "catalog-executor" and row["status"] == "accepted"
    # ключа `rule` (машинная форма правила авиакомпании) в строке нет ни
    # на одном уровне; `rule_form` записи каталога — обезличенная форма
    # правила самой записи и остаётся
    assert "rule" not in row
    assert '"rule":' not in json.dumps(row, ensure_ascii=False)
    assert "разборе" not in json.dumps(row, ensure_ascii=False)
    assert row["record"]["catalog_fingerprint"] == cat.fingerprint()
    assert [x["kind"] for x in row["spec"]["executors_requested"]] \
        == ["check_code"]
    assert row["work"]["candidate"] == \
        "catalog/candidates/families/duty_limits.json"


def test_каталог_kotlin_виды_идут_агенту(iso, catalog_copy):
    """§7 п. 18. Дорожка «6-К» снята словом владельца 08.09.2026: виды
    исполнителей решателя входят в перечень к реализации, строка
    рождается принятой, отложенных видов не бывает, а белый список путей
    открывает таблицы решателя и закрывает инструмент измерения.

    «Нужен человек» при рождении остаётся ровно в одном случае — заявка
    не назвала ни одного вида."""
    from vmp import catalog_access as ca
    from webapp import catalog_pipeline as cp
    rq = _request(kinds=("kotlin_knob",))
    catalog_copy([rq])
    row = cp.build_row(rq, ca.current())
    assert row["status"] == "accepted"
    assert [x["kind"] for x in row["spec"]["executors_requested"]] \
        == ["kotlin_knob"]
    assert row["spec"]["executors_deferred"] == []
    files = row["work"]["allowed_files"]
    assert any(f.endswith("/RunThresholds.kt") for f in files)
    assert not any(f.endswith("/PackagedRuns.kt") for f in files)
    assert row["work"]["commands"][0].startswith(
        "python -m vmp.solver_build check")
    assert "6-К" not in json.dumps(row, ensure_ascii=False)
    # решатель больше не закрыт целиком, но инструмент измерения закрыт
    forbidden = row["work"]["forbidden_paths"]
    assert "kotlin-solver/" not in forbidden
    assert any(p.endswith("/rest/") for p in forbidden)
    assert any(p.endswith("PackagedRuns.kt") for p in forbidden)
    assert "vmp/stand_solver.py" in forbidden
    assert "vmp/solver_build.py" in forbidden
    # белый список и гейт символов не расходятся: пять файлов, в которых
    # ищут символ строки посева, агенту открыты
    from vmp import bindings_gate as bg
    assert cp.KOTLIN_SOLVER_DIR == bg.KOTLIN_DIR + "/"
    for path in (bg.PROVIDER_FILE, bg.RUN_THRESHOLDS_FILE,
                 bg.TASK_PACKAGE_FILE, bg.LOGIC_FORMS_FILE,
                 bg.TASK_MODES_FILE):
        assert path in files, path
    # заявка без единого вида: реализовывать нечего (записи ещё нет в
    # каталоге, и перечень видов пуст — брать его неоткуда)
    rq0 = _request(record_id="novaya_zapis", kinds=("check_code",))
    row0 = cp.build_row({**rq0, "executor_kinds_requested": []},
                        ca.current())
    assert row0["status"] == "needs_human"
    assert "не называет ни одного вида" in row0["impl"]["report"]
    crq._add_crs([{**row0, "status": "accepted"}], unique_by_id=True)
    with pytest.raises(crq.ChangeRequestError) as e:
        crq.implement_cr(row0["id"], ADMIN)
    assert "ни одного вида" in str(e.value)


def test_каталог_строка_прежней_дорожки_переоткрывается_сверкой(
        iso, catalog_copy, external, monkeypatch):
    """§7 п. 19. Строку, закрытую прежней дорожкой «6-К», сверка
    переоткрывает принятой: решение по заявке владелец уже принял, и
    просить его нажать «принять» второй раз нельзя. Строку с веткой (в
    ней есть работа агента) сверка не трогает."""
    from vmp import catalog_access as ca
    from webapp import catalog_pipeline as cp
    rq = _request(kinds=("kotlin_knob",))
    catalog_copy([rq])
    _service(monkeypatch, "edit", ca.current().fingerprint(), [])
    row = cp.build_row(rq, ca.current())
    old = {**row, "status": "needs_human",
           "title": "Исполнитель записи families/duty_limits: видов Python "
                    "нет",
           "impl": {"needs_human": True,
                    "report": "все виды — исполнители решателя (kotlin_*): "
                              "дорожка 6-К по слову владельца"}}
    crq._add_crs([old], unique_by_id=True)
    out = cp.sync_catalog_requests()
    got = crq.get_cr(row["id"])
    assert got["status"] == "accepted", out
    assert not got.get("impl")
    assert any("слово владельца 08.09.2026" in str(j.get("words"))
               for j in got["journal"])
    assert [x["kind"] for x in got["spec"]["executors_requested"]] \
        == ["kotlin_knob"]
    # строка с веткой — работа агента: её сверка не переоткрывает
    crq._update_cr(row["id"], {
        "status": "needs_human",
        "impl": {"needs_human": True, "branch": "cr-x",
                 "report": "дорожка 6-К"}})
    cp.sync_catalog_requests()
    assert crq.get_cr(row["id"])["status"] == "needs_human"


def test_каталог_сверка_открывает_строку_и_пишет_ключ(
        iso, catalog_copy, external, monkeypatch):
    from vmp import catalog_access as ca
    from webapp import catalog_pipeline as cp
    rq = _request()
    catalog_copy([rq])
    calls = []
    _service(monkeypatch, "edit", ca.current().fingerprint(), calls)
    out = cp.sync_catalog_requests()
    cid = crq.list_crs()[0]["id"]
    assert out["opened"] == [cid] and out["keyed"] == [cid]
    assert calls == [(rq["request_id"], cid)]
    # идемпотентность: повтор не плодит строк и не переписывает журнал
    before = json.dumps(crq.list_crs(), ensure_ascii=False)
    out2 = cp.sync_catalog_requests()
    assert out2["opened"] == [] and len(crq.list_crs()) == 1
    assert json.dumps(crq.list_crs(), ensure_ascii=False) == before


def test_каталог_служба_в_просмотре_ключ_не_пишет(
        iso, catalog_copy, external, monkeypatch):
    from vmp import catalog_access as ca
    from webapp import catalog_pipeline as cp
    catalog_copy([_request()])
    calls = []
    _service(monkeypatch, "view", ca.current().fingerprint(), calls)
    out = cp.sync_catalog_requests()
    assert len(out["opened"]) == 1 and out["keyed"] == [] and calls == []
    assert any("рабочее место владельца" in p for p in out["problems"])


def test_каталог_иной_отпечаток_службы_ключ_не_пишет(
        iso, catalog_copy, external, monkeypatch):
    from webapp import catalog_pipeline as cp
    catalog_copy([_request()])
    calls = []
    _service(monkeypatch, "edit", "f" * 64, calls)
    out = cp.sync_catalog_requests()
    assert calls == [] and out["keyed"] == []
    assert any("другой каталог" in p for p in out["problems"])


def test_каталог_отклонение_зеркалит_решение_владельца(
        iso, catalog_copy, external, monkeypatch):
    """Строка каталога закрывается ТОЛЬКО решением владельца по заявке:
    администратору ручка отказывает словами, а сверка отклоняет её сама;
    строку в работе сверка не трогает — её отклоняют после завершения."""
    from vmp import catalog_access as ca
    from webapp import catalog_pipeline as cp
    rq = _request()
    path = catalog_copy([rq])
    _service(monkeypatch, "edit", ca.current().fingerprint(), [])
    cp.sync_catalog_requests()
    cid = crq.list_crs()[0]["id"]
    with pytest.raises(crq.ChangeRequestError) as e:
        crq.reject_cr(cid, ADMIN, "не хочу")
    assert "решением владельца" in str(e.value)
    rq_off = {**rq, "state": "отклонена"}
    (path / "requests.json").write_text(
        json.dumps({"form_version": 1, "requests": [rq_off]},
                   ensure_ascii=False), encoding="utf-8")
    ca.forget()
    out = cp.sync_catalog_requests()
    assert out["rejected"] == [cid]
    assert crq.get_cr(cid)["status"] == "rejected"
    # строка в работе не трогается: журнал говорит «после завершения»
    crq._update_cr(cid, {"status": "implementing"})
    out2 = cp.sync_catalog_requests()
    assert crq.get_cr(cid)["status"] == "implementing"
    assert any("после завершения" in j["words"]
               for j in crq.get_cr(cid)["journal"])
    assert cid in out2["skipped"]


def test_каталог_внутренний_контур_строк_не_открывает(
        iso, catalog_copy, monkeypatch):
    """Реализация CR зовёт облачную модель и на внутреннем контуре
    запрещена — строк очереди сверка там не заводит, только отчёт."""
    from vmp import catalog_access as ca
    from webapp import catalog_pipeline as cp
    catalog_copy([_request()])
    _service(monkeypatch, "edit", ca.current().fingerprint(), [])
    out = cp.sync_catalog_requests()
    assert crq.list_crs() == [] and out["opened"] == []
    assert any("внутренний контур" in p for p in out["problems"])


#: Слепок дерева решателя в клоне: ровно те файлы, которые нужны судьям
#: белого списка, судье диапазона и судье токенов (пакет
#: «Kotlin-исполнители агентом», §2). Настоящего решателя в клоне нет —
#: его собирает и поднимает подставной `vmp.solver_build`.
_SOLVER_DIR = ("kotlin-solver/src/main/kotlin/org/acme/flighcrewscheduling/"
               "solver")
_SOLVER_TESTS = "kotlin-solver/src/test/kotlin/org/acme/flighcrewscheduling"
_TASK_MODES = """\
package org.acme.flighcrewscheduling.solver

data class MetricExecutor(val constraint: String, val tasks: Set<String>)

val METRIC_EXECUTORS: Map<String, MetricExecutor> = mapOf(
    "credit_hours_spread" to MetricExecutor(
        constraint = "Credit hours spread over threshold",
        tasks = setOf("roster"),
    ),
)

fun modeOf(name: String): String = name
"""


def _kotlin_tree(repo) -> None:
    """Разложить слепок решателя по клону."""
    files = {
        f"{_SOLVER_DIR}/FlightCrewSchedulingConstraintProvider.kt":
            "package org.acme.flighcrewscheduling.solver\n\nclass P\n",
        f"{_SOLVER_DIR}/RunThresholds.kt":
            "package org.acme.flighcrewscheduling.solver\n\nobject R\n",
        f"{_SOLVER_DIR}/TaskModes.kt": _TASK_MODES,
        f"{_SOLVER_DIR}/PackagedRuns.kt":
            "package org.acme.flighcrewscheduling.solver\n\nobject Runs\n",
        "kotlin-solver/src/main/kotlin/org/acme/flighcrewscheduling/rest/"
        "FlightCrewSchedulingResource.kt":
            "package org.acme.flighcrewscheduling.rest\n\nclass Res\n",
        "kotlin-solver/src/main/resources/application.properties":
            "quarkus.http.port=8099\n",
        "kotlin-solver/pom.xml": "<project/>\n",
        f"{_SOLVER_TESTS}/solver/CrewSizeConstraintTest.kt":
            "package org.acme.flighcrewscheduling.solver\n\nclass T\n",
    }
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@pytest.fixture()
def catalog_repo(tmp_path, monkeypatch, catalog_copy, solver_stub):
    """Клон Test со срезом шагов 4–7: код каталога, стенд, посев
    привязок, слепок дерева решателя и КАТАЛОГ КЛОНА — та же копия, на
    которую смотрит дверь (иначе предпосылка дрейфа отказывает по
    построению). Сборка решателя и эфемерный решатель подставные:
    `solver_stub` включён у каждого теста реализации."""
    from pathlib import Path
    repo = tmp_path / "clone"
    repo.mkdir()

    def g(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True)
    g("init", "-b", "main")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (repo / "template_catalog").mkdir()
    (repo / "template_catalog" / "__init__.py").write_text(
        "", encoding="utf-8")
    (repo / "vmp").mkdir()
    (repo / "vmp" / "stand_tasks.py").write_text("# стенд\n",
                                                 encoding="utf-8")
    (repo / "vmp" / "criteria.py").write_text("# исполнитель\n",
                                              encoding="utf-8")
    (repo / "vmp" / "bindings_seed.json").write_text(json.dumps(
        {"form_version": 1, "pending_records": [],
         "records": {"families/duty_limits": [
             {"kind": "check_code", "state": "есть",
              "file": "vmp/criteria.py", "symbol": "_landings_per_duty"}]}},
        ensure_ascii=False), encoding="utf-8")
    # Папка попыток снята с учёта git — как в продукте: иначе `git clean`
    # уносил бы свидетельство попытки, а `git status` считал бы клон
    # грязным перед следующим нажатием.
    (repo / ".gitignore").write_text("_repro/\n", encoding="utf-8")
    # Мера регрессии по соседям (0.7): на main у клона есть строки
    # стенда, против которых конвейер считает числа.
    (repo / "vmp" / "stand_results.json").write_text(json.dumps(
        {"form_version": 1, "results": [
            {"record": "families/duty_limits", "executor_kind": "check_code",
             "task": "st-duty-1", "result": "passed"}]},
        ensure_ascii=False), encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_run_button.py").write_text("# тест\n",
                                                       encoding="utf-8")
    _kotlin_tree(repo)
    catalog_copy([], at=repo)                     # каталог клона = дверь
    g("add", "-A")
    g("commit", "-m", "init")
    from webapp import evolution_runner as ev
    monkeypatch.setenv("COSMOS_TEST_DIR", str(repo))
    monkeypatch.setenv("COSMOS_PROD_DIR", str(tmp_path / "prod"))
    monkeypatch.setenv("COSMOS_STAGE_DIR", str(tmp_path / "stage"))
    return repo, ev, Path


def _catalog_cr(repo, rq):
    """Заявка — в каталог клона (и в его коммит: дерево обязано остаться
    чистым), строка CR — в очередь."""
    from vmp import catalog_access as ca
    from webapp import catalog_pipeline as cp
    (repo / "catalog" / "requests.json").write_text(
        json.dumps({"form_version": 1, "requests": [rq]},
                   ensure_ascii=False), encoding="utf-8")
    for args in (["add", "-A"], ["commit", "-m", "заявка каталога"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True)
    ca.forget()
    row = cp.build_row(rq, ca.current())
    crq._add_crs([row], unique_by_id=True)
    return row


def _candidate_of(repo, rq, cid, tasks=None, kinds=()):
    """Файл-кандидат, который пишет агент: запись каталога целиком со
    статусом «есть», без истории (её пишет сборка службой).

    `tasks` — свои стендовые задачи (различающие пары и близнецы),
    `kinds` — виды, которые запись обязана объявлять."""
    from vmp import catalog_access as ca
    rec = ca.current().family("duty_limits")
    rec.pop("history", None)
    rec["status"] = "есть"
    if tasks is not None:
        rec["stand_tasks"] = list(tasks)
    if kinds:
        rec["executor_kinds"] = sorted(set(rec.get("executor_kinds") or ())
                                       | set(kinds))
    path = repo / "catalog" / "candidates" / "families" / "duty_limits.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"form_version": 1, "section": "families", "id": "duty_limits",
         "record": rec, "note": "исполнитель дописан конвейером",
         "by": "контур самоулучшения",
         "saved_at": "2026-09-12T12:00:00+03:00",
         "request_id": rq["request_id"], "cr_key": cid},
        ensure_ascii=False, indent=1), encoding="utf-8")
    return path


#: Снимок таблиц решателя (`vmp.kotlin_tables.tables()`) — девять ключей
#: контракта дорожки А. Тот же снимок берётся и на main, и на ветке:
#: рост таблиц тесты задают подставным `tables_growth`.
_TABLES_MAIN = {"knobs": {"max_landings_per_duty": 6}, "sit_knobs": {},
                "sit_param": "sit_minutes", "constants": {},
                "history_families": [], "history_units": {},
                "family_to_constraint": {}, "logic_forms": {},
                "metrics": {}}

#: Пустой рост таблиц: класс правки — К-исп (0.9).
_NO_GROWTH = {k: [] for k in ("knobs", "sit_knobs", "sit_param", "constants",
                              "history_families", "history_units",
                              "family_to_constraint", "logic_forms",
                              "metrics")}


class FakeSolver:
    """Подставной эфемерный решатель (§7): журнал start/ready/stop и
    адрес `http://127.0.0.1:1`. Живой JVM в тестах нет — судятся порядок
    цепочки, адрес в командах стенда и остановка в `finally`."""

    URL = "http://127.0.0.1:1"

    def __init__(self, artifact_dir, *, cr_key, log_path, java_home=None,
                 xmx="1g", start_timeout_s=120.0, tag=None,
                 fail_start=False):
        self.artifact_dir = str(artifact_dir)
        self.cr_key = cr_key
        self.java_home = java_home
        #: Признак контура в метке: сборщик сирот соседнего контура нашу
        #: JVM по метке не находит.
        self.tag = tag
        self.log_path = log_path
        self.log = []
        self.stopped = False
        self.fail_start = fail_start

    def __enter__(self):
        self.log.append("start")
        if self.fail_start:
            raise RuntimeError("JVM не поднялась: адрес занят")
        self.log.append("ready")
        return self.URL

    def __exit__(self, *exc):
        self.stop()
        return False

    def alive(self):
        return not self.stopped

    def stop(self):
        self.log.append("stop")
        self.stopped = True

    def report(self):
        return {"url": self.URL, "port": 1, "pid": 4242,
                "pid_matches_port": True, "attempts": 1,
                "jvm": ["-Xmx1g", "-Dcosmos.ephemeral="
                        + (f"{self.tag}:" if self.tag else "")
                        + f"{self.cr_key}"],
                "env": "белый список", "started_in_s": 1.4,
                "stopped": "terminated" if self.stopped else "не остановлен",
                "port_free_after": True,
                "log_tail": "started in 1.1s",
                "words": "эфемерный решатель поднят и остановлен"}


@pytest.fixture()
def solver_stub(monkeypatch):
    """Подставной `vmp.solver_build` (модуля дорожки А может ещё не быть
    в дереве) и подставной `crq._solver_factory` — две точки подмены §7.

    Возврат — словарь состояния: тест правит `probe`, `hygiene`,
    `growth`, `fail_start` и читает `solvers` (созданные решатели) и
    `orphans` (сколько раз звали сборщик сирот)."""
    import sys
    import types

    import vmp
    state = {
        "probe": {"ok": True, "words": "проба тулчейна: всё на месте"},
        "hygiene": {"ok": True, "tests_main": 367, "tests_branch": 368,
                    "new_files": [f"{_SOLVER_TESTS}/solver/NewTest.kt"],
                    "changed_existing": [], "resources": [],
                    # Столько же, сколько даст настоящая гигиена на
                    # дереве: условная аннотация `@EnabledIfSystemProperty`
                    # у LogicRollingBenchTest даёт surefire `skipped=1`, и
                    # мера базы обязана считать ту же семью — подделка,
                    # ровняющая числа «под зелёное», прятала бы судью.
                    "skipped_main": 1, "disabled_added": 0,
                    "problems": [], "words": "гигиена зелёная"},
        "growth": dict(_NO_GROWTH),
        "fail_start": False, "solvers": [], "orphans": 0,
        "orphan_markers": [],
    }

    def scrub(text, roots=None):
        out = str(text)
        for root in sorted((roots or []), key=len, reverse=True):
            for variant in (str(root), str(root).replace("\\", "/")):
                out = out.replace(variant, "<путь>")
        return re.sub(r"(?<![A-Za-z])[A-Za-z]:[\\/][^\s\"'()]+",
                      "<путь>", out)

    def reap(marker="-Dcosmos.ephemeral=", since=None):
        state["orphans"] += 1
        state["orphan_markers"].append(str(marker))
        return []

    mod = types.ModuleType("vmp.solver_build")
    mod.MARKER = "-Dcosmos.ephemeral="
    mod.contour_tag = lambda repo: "конт7"
    mod.contour_marker = lambda repo: mod.MARKER + mod.contour_tag(repo) + ":"
    mod.probe = lambda repo, **kw: state["probe"]
    mod.test_hygiene = lambda repo, base, literals: state["hygiene"]
    mod.tables_growth = lambda before, after: state["growth"]
    mod.reap_orphans = reap
    mod.scrub = scrub
    mod.EphemeralSolver = FakeSolver
    monkeypatch.setitem(sys.modules, "vmp.solver_build", mod)
    monkeypatch.setattr(vmp, "solver_build", mod, raising=False)

    def factory(artifact_dir, **kw):
        kw.pop("fail_start", None)
        solver = FakeSolver(artifact_dir, fail_start=state["fail_start"],
                            **kw)
        state["solvers"].append(solver)
        return solver
    monkeypatch.setattr(crq, "_solver_factory", factory)
    return state


def _build_json(**over):
    """Отчёт сборки решателя по контракту дорожки А (§4 `build.*`)."""
    doc = {"available": True, "ok": True, "source": "commit",
           "commit": "0" * 40, "attempt": "attempt-1",
           "command_package": "kotlin-solver/mvnw.cmd -o -B -DskipTests "
                              "package",
           "command_test": "kotlin-solver/mvnw.cmd -o -B -DargLine=-Xmx1g "
                           "test",
           "rc_package": 0, "rc_test": 0,
           "duration_package_s": 31.4, "duration_test_s": 62.0,
           "toolchain": {"java": "openjdk version \"21.0.11\"",
                         "maven": "обёртка 3.3.4 (оффлайн)",
                         "maven_opts": "-Xmx2g", "kotlin_daemon": False},
           "versions": {"quarkus": "3.34.5", "kotlin": "2.3.20"},
           "surefire": {"tests": 368, "failures": 0, "errors": 0,
                        "skipped": 1, "classes": 36},
           "test_annotations": {"branch": 368, "files": 36},
           "tests_match_sources": True,
           "artifact": {"dir": "<попытка>/kotlin-solver/target/quarkus-app",
                        "app_jar": "quarkus-app/app/solver-1.0.0.jar",
                        "app_jar_sha256": "a" * 64,
                        "run_jar_sha256": "b" * 64,
                        "dependencies_txt_sha256": "c" * 64,
                        "tree_sha256": "d" * 64,
                        "unchanged_after_tests": True},
           "hs_err": [], "compiler_daemons_killed": 0,
           "log_tail": "BUILD SUCCESS", "words": "сборка зелёная"}
    doc.update(over)
    return doc


def _parity_json(**over):
    """Вердикт закона паритета по контракту `catalog/tools/PARITY-CONTRACT.md`
    §2 — форма, на которой дорожка В судит конвейер.

    Движок `catalog/tools/parity.py` — дорожка А, и до её сдачи вердикт
    подделывается по подстроке `parity.py` в `ev._run`. Живой контракт
    argv судит `test_каталог_команды_гейтов_принимают_настоящие_программы`
    настоящим подпроцессом."""
    doc = {"rc": 0, "ok": True,
           "catalog": {"version": "0.2.1-draft.1", "fingerprint": "f" * 64,
                       "path": "<временный каталог попытки>",
                       "bindings": "<попытка>/bindings.json",
                       "with_candidates": ["families/duty_limits"]},
           "growth_judged": {"knobs": "L05/L18", "metrics": "L08/L22"},
           "counts": {"rules": 23, "green": 22, "red": 0, "skipped": 1,
                      "warning": 0},
           "rules": {"L05": {"ok": True, "words": None,
                             "numbers": {"params": 47, "cdpm": 44,
                                         "knobs": 23}},
                     # L21 «запись ≡ заявке» — единственный независимый
                     # якорь владельца: гейт зовёт движок с ключом
                     # `--candidate`, и правило обязано СУДИТЬ. Пропуск
                     # здесь конвейер красит (`_parity_verdict`).
                     "L21": {"ok": True,
                             "numbers": {"candidates": 1, "judged": 1},
                             "beyond_request": [
                                 "params: +max_night_sits (count, max)"]},
                     "L23": {"ok": None, "skipped": "папка без tools/ — "
                                                    "свежесть носителей "
                                                    "судит дерево"}},
           "words": None}
    doc.update(over)
    return doc


def _stand_row(**over):
    """Строка файла результатов стенда."""
    row = {"record": "families/duty_limits", "executor_kind": "check_code",
           "task": "st-duty-1", "result": "passed", "note": "",
           "expected": {}, "got": {}, "run_id": "run-1"}
    row.update(over)
    return row


#: Снимок свидетелей паритета `vmp/run/roster_certify.py` на main
#: (просьба В-2): перечни, по которым правила L15/L16/L09 судят природу
#: записи, её условия и сертификатные шаблоны проверок.
_CERTIFY_MAIN = {
    "PEOPLE_FAMILIES": ["crew_quals"],
    "WORLD_PARAM_FAMILIES": ["turnaround_rules"],
    "EXTENSION_CONDITIONS": {"duty_extension_reason": ["col", "in"]},
    "SOFT_MEASURE_TEMPLATE": "мягкая-мера",
    "WORLD_PARAM_TEMPLATE": "параметр-мира",
}


def _catalog_run_mock(repo, ev, effect, *, stand_result="passed",
                      pytest_rc=0, rows=None, build=None, tables=None,
                      bindings=None, calls=None, pytest_out=None,
                      build_side=None, parity=None, certify=None,
                      commit_rc=None):
    """ev._run для CR каталога: git — по-настоящему, CLI — правка агента,
    сборка решателя, снимок таблиц, команды цепочки и гейтов — свои
    файлы результатов.

    `calls` (если передан) собирает (аргументы, `scrub_prefixes`,
    `env_extra`) каждой команды: порядок цепочки и окружение
    подпроцессов судятся по нему.

    `commit_rc` — отказ `git commit` этим кодом (128 = «Author identity
    unknown» в клоне без личности автора): единственный способ увидеть
    шов, на котором конвейер прежде шёл дальше без коммита."""
    from pathlib import Path

    certify_calls: list = []

    def at(cwd, value):
        p = Path(str(value))
        return p if p.is_absolute() else Path(str(cwd)) / p

    def fake_run(cmd, cwd, timeout_s, env_extra=None, input_text=None,
                 on_line=None, scrub_prefixes=()):
        cmd = [str(c) for c in cmd]
        if calls is not None:
            calls.append({"cmd": cmd, "scrub": tuple(scrub_prefixes or ()),
                          "env": dict(env_extra or {}), "cwd": str(cwd),
                          "timeout_s": timeout_s})
        if cmd[0] == "git":
            if commit_rc is not None and "commit" in cmd:
                return commit_rc, (
                    "Author identity unknown\n\n*** Please tell me who you "
                    "are.\n\nfatal: unable to auto-detect email address")
            p = subprocess.run(["git", "-C", str(cwd), *cmd[1:]],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        if cmd[0] == ev.claude_cli():
            return effect()
        joined = " ".join(cmd)
        if "kotlin_tables" in joined:
            return 0, json.dumps(tables if tables is not None
                                 else _TABLES_MAIN, ensure_ascii=False)
        if "roster_certify" in joined:
            # Снимок свидетелей паритета (просьба В-2): перечни, по
            # которым закон судит природу записи, её условия и
            # сертификатные шаблоны. `certify` — снимок ПОСЛЕ работы
            # агента: так стенд показывает правку свидетеля.
            certify_calls.append(1)
            doc = (certify if certify is not None and len(certify_calls) > 1
                   else _CERTIFY_MAIN)
            return 0, json.dumps(doc, ensure_ascii=False)
        if "vmp.solver_build" in joined:
            out = at(cwd, cmd[cmd.index("--json") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            # Настоящая сборка оставляет сотни мегабайт в `target/`
            # папки попытки — подделка кладёт туда файл, чтобы удаление
            # этой папки судилось, а не подразумевалось.
            target = (at(cwd, cmd[cmd.index("--attempt") + 1])
                      / "kotlin-solver" / "target" / "quarkus-app")
            target.mkdir(parents=True, exist_ok=True)
            (target / "quarkus-run.jar").write_bytes(b"run")
            if build_side is not None:
                build_side()
            doc = _build_json() if build is None else dict(build)
            out.write_text(json.dumps(doc, ensure_ascii=False),
                           encoding="utf-8")
            return (0 if doc.get("available") and not doc.get("rc_test")
                    and not doc.get("rc_package") else 1), "сборка"
        if "vmp.stand_tasks" in joined:
            out = at(cwd, cmd[cmd.index("--write-results") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            body = (rows if rows is not None
                    else [_stand_row(result=stand_result)])
            out.write_text(json.dumps(
                {"results": body,
                 "solver": {"url": "http://127.0.0.1:1",
                            "package_fingerprint": None}},
                ensure_ascii=False), encoding="utf-8")
            return 0, "стенд"
        if "vmp.bindings_build" in joined:
            out = at(cwd, cmd[cmd.index("--out") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            body = bindings if bindings is not None else [
                {"kind": "check_code", "state": "есть",
                 "file": "vmp/criteria.py",
                 "symbol": "_landings_per_duty", "cr_key": None}]
            out.write_text(json.dumps({"bindings": [
                {"section": "families", "id": "duty_limits",
                 "executors": body}]}, ensure_ascii=False),
                encoding="utf-8")
            return 0, "привязки"
        if "vmp.bindings_gate" in joined:
            return 0, "гейт привязок"
        if "template_catalog.candidates" in joined:
            return 0, "проблем 0"
        if "parity.py" in joined:
            # Пятый гейт: движок пишет вердикт файлом `--json`, а код
            # возврата дублирует поле `rc` (контракт §1 и §2).
            out = at(cwd, cmd[cmd.index("--json") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            doc = _parity_json() if parity is None else dict(parity)
            # `catalog.path` движок пишет АБСОЛЮТНЫМ путём судимой папки
            # (контракт §2) — подделка обязана делать то же, иначе шов
            # «свидетельство идёт через скруббер» никем не судился бы.
            doc["catalog"] = dict(doc.get("catalog") or {})
            doc["catalog"]["path"] = str(at(cwd,
                                            cmd[cmd.index("--catalog") + 1]))
            out.write_text(json.dumps(doc, ensure_ascii=False),
                           encoding="utf-8")
            return int(doc.get("rc") or 0), "паритет"
        if "pytest" in joined:
            if pytest_out is not None:
                return pytest_rc, pytest_out
            return pytest_rc, "1 passed" if pytest_rc == 0 else "1 failed"
        return 0, ""
    return fake_run


def test_каталог_реализация_два_коммита_и_свидетельство(
        iso, catalog_repo, monkeypatch):
    """Порядок §3.4: работа агента коммитится ДО гейтов содержания, за
    ней — коммит свидетельства; кандидат несёт блок evidence, клон
    остаётся чистым на main."""
    repo, ev, Path = catalog_repo
    rq = _request()
    row = _catalog_cr(repo, rq)

    def effect():
        (repo / "vmp" / "criteria.py").write_text("# исполнитель 2\n",
                                                  encoding="utf-8")
        _candidate_of(repo, rq, row["id"])
        return 0, "дописал исполнителя"
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(repo, ev, effect))
    out = crq.implement_cr(row["id"], ADMIN)
    assert out["status"] == "implemented", out["impl"]["gates_report"]
    g = out["impl"]["gates_report"]
    assert g["paths"] == "ok" and g["chain_live"] == "ok"
    assert g["pytest"] == "green" and g["stand"]["passed"] == 1
    assert g["present_rows"] == ["check_code"]
    assert out["impl"]["commit"] and out["impl"]["evidence_commit"]
    n = subprocess.run(["git", "-C", str(repo), "rev-list", "--count",
                        "main..cr-" + row["id"]], capture_output=True,
                       text=True, encoding="utf-8", errors="replace").stdout.strip()
    assert n == "2"
    # кандидат живёт в ВЕТКЕ (клон вернулся на main) — читаем оттуда
    shown = subprocess.run(
        ["git", "-C", str(repo), "show",
         "cr-" + row["id"] + ":catalog/candidates/families/duty_limits.json"],
        capture_output=True, text=True, encoding="utf-8")
    ev_doc = json.loads(shown.stdout)
    # свидетельство есть, машинного пути временной папки в нём нет
    assert ev_doc["evidence"]["cr_key"] == row["id"]
    assert "tmp" not in ev_doc["evidence"]["gates"]
    st = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
    assert st == ""


def test_каталог_красный_стенд_не_стирает_работу_агента(
        iso, catalog_repo, monkeypatch):
    repo, ev, _ = catalog_repo
    rq = _request()
    row = _catalog_cr(repo, rq)

    def effect():
        (repo / "vmp" / "criteria.py").write_text("# 3\n", encoding="utf-8")
        _candidate_of(repo, rq, row["id"])
        return 0, "готово"
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(
        repo, ev, effect, stand_result="failed"))
    out = crq.implement_cr(row["id"], ADMIN)
    assert out["status"] == "needs_human"
    assert out["impl"]["gates_report"]["stand"]["failed"] == 1
    n = subprocess.run(["git", "-C", str(repo), "rev-list", "--count",
                        "main..cr-" + row["id"]], capture_output=True,
                       text=True, encoding="utf-8", errors="replace").stdout.strip()
    assert n == "2"                       # работа агента сохранена


def test_каталог_отказ_коммита_работы_агента_говорит_словами(
        iso, catalog_repo, monkeypatch):
    """Отказ `git commit` — честный `needs_human`, а не молчаливый проход.

    Прежде код возврата коммита не читался: в клоне без личности автора
    git отвечал 128, цепочка судила БАЗОВУЮ голову, сторож дерева
    краснил файлы агента как грязь, а уборка их стирала (сквозная
    проверка 08.09.2026). Теперь исход коммита проверяется, а работа
    агента остаётся человеку патчем в папке попытки."""
    repo, ev, Path = catalog_repo
    rq = _request()
    row = _catalog_cr(repo, rq)

    def effect():
        (repo / "vmp" / "criteria.py").write_text("# исполнитель\n# ещё\n",
                                                  encoding="utf-8")
        _candidate_of(repo, rq, row["id"])
        return 0, "дописал исполнителя"
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(
        repo, ev, effect, commit_rc=128))
    out = crq.implement_cr(row["id"], ADMIN)
    assert out["status"] == "needs_human"
    g = out["impl"]["gates_report"]
    assert "работа агента не закоммичена" in g["commit"]
    assert "код 128" in g["commit"] and "identity" in g["commit"]
    # Сборки решателя и живого суда не было — блок решателя говорит это
    # словами, а не молчит пустыми полями.
    assert g["solver"]["build"]["available"] is False
    assert "работа агента не закоммичена" in g["solver"]["words"]
    # Работа агента спасена: патч лежит в папке попытки, и в нём —
    # правка агента. `_repro/` закрыта `.gitignore`, поэтому уборка
    # клона (`reset --hard` + `clean -fd`) его не унесла.
    patches = sorted((repo / "_repro" / f"catalog-cr-{row['id']}")
                     .glob("attempt-*/agent-work.patch"))
    assert len(patches) == 1, patches
    body = patches[0].read_text(encoding="utf-8", errors="replace")
    assert "vmp/criteria.py" in body and "# ещё" in body
    assert "работа агента сохранена патчем" in g["commit"]
    # Причина видна ТАМ, ГДЕ её читает человек: карточка показывает
    # первые 400 знаков `impl.report`, и слова стоят в начале.
    assert out["impl"]["report"][:400].startswith(
        "работа агента не закоммичена")
    # Клон убран: чистое дерево на main, пустая ветка снесена.
    st = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace").stdout.strip()
    assert st == ""


def test_каталог_коммит_состоится_в_клоне_без_личности_автора(
        iso, catalog_repo, monkeypatch):
    """Личность автора конвейер везёт СВОЮ, аргументами вызова.

    Клон контура — машинный, и `user.name`/`user.email` в нём может не
    быть вовсе (ни своих, ни общих). Прежде такой клон ронял коммит
    работы агента; теперь коммит состоится, а автором стоит агент."""
    repo, ev, Path = catalog_repo
    rq = _request()
    row = _catalog_cr(repo, rq)
    # Ни своей личности, ни общей: общий и системный файлы настроек
    # уводятся на несуществующие пути — иначе тест зависел бы от машины,
    # на которой его запустили. Перед этим НАСТРОЙКИ ПЕРЕВОДА КОНЦОВ
    # СТРОК переносятся в клон: на Windows `core.autocrlf` живёт в общем
    # файле, и без переноса git после подмены сравнивал бы рабочее дерево
    # (CRLF) с индексом (LF) и объявлял бы клон грязным — предпосылка
    # реализации отказала бы вместо проверяемого шва.
    for key in ("core.autocrlf", "core.eol"):
        got = subprocess.run(["git", "-C", str(repo), "config", "--get", key],
                             capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        if got.returncode == 0 and got.stdout.strip():
            subprocess.run(["git", "-C", str(repo), "config", key,
                            got.stdout.strip()], capture_output=True)
    for args in (["config", "--unset", "user.email"],
                 ["config", "--unset", "user.name"]):
        subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(repo / "нет-такого"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(repo / "нет-такого"))
    # Клон обязан остаться ЧИСТЫМ после подмены — иначе тест доказывал бы
    # не то: предпосылку реализации, а не личность автора.
    st = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace").stdout.strip()
    assert st == "", f"клон загрязнился подменой настроек git: {st[:300]}"

    def effect():
        (repo / "vmp" / "criteria.py").write_text("# исполнитель 5\n",
                                                  encoding="utf-8")
        _candidate_of(repo, rq, row["id"])
        return 0, "дописал исполнителя"
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(repo, ev, effect))
    out = crq.implement_cr(row["id"], ADMIN)
    assert out["status"] == "implemented", out["impl"]["gates_report"]
    assert out["impl"]["commit"] and out["impl"]["evidence_commit"]
    author = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%an <%ae>",
         "cr-" + row["id"]], capture_output=True, text=True,
        encoding="utf-8", errors="replace").stdout.strip()
    assert author == f"{crq.CR_AGENT_NAME} <{crq.CR_AGENT_EMAIL}>"


def test_каталог_команды_гейтов_принимают_настоящие_программы(
        iso, tmp_path, catalog_copy):
    """Контракт argv гейтов: их принимают НАСТОЯЩИЕ программы.

    Тесты реализации подменяют `ev._run` и потому судят порядок шагов и
    форму отчёта, но не ключи командной строки — на этом шве и
    разошлись `--records` с `--write-results` (стенд отказывает кодом 2:
    tests/vmp/test_stand_tasks.py::test_records_с_write_results_отказ).
    Здесь argv строит тот же `catalog_gate_commands`, а исполняют их
    настоящие модули подпроцессами на материализованном кандидате.

    Файл результатов — свидетельство ВСЕГО временного каталога, поэтому
    строк в нём больше, чем у записи: суд по записи делает фильтр
    `_stand_counts(addr)`, и это проверяется здесь же."""
    import os
    import sys
    from pathlib import Path
    from template_catalog import candidates as tc
    from vmp import catalog_access as ca
    copy_dir = catalog_copy([])                    # tmp-копия каталога
    rec = ca.current().family("duty_limits")
    rec.pop("history", None)
    rec["status"] = "есть"
    cand_path = tmp_path / "duty_limits.json"
    cand_path.write_text(json.dumps(
        {"form_version": 1, "section": "families", "id": "duty_limits",
         "record": rec, "note": "исполнитель дописан конвейером",
         "by": "контур самоулучшения",
         "saved_at": "2026-09-12T12:00:00+03:00",
         "request_id": _request()["request_id"], "cr_key": "cr0be5a1f2"},
        ensure_ascii=False, indent=1), encoding="utf-8")
    gate_tmp = tmp_path / "gate"
    tc.materialize(copy_dir, tc.load_candidate(cand_path),
                   out_dir=gate_tmp / "catalog")
    cmds = crq.catalog_gate_commands(sys.executable, copy_dir.parent,
                                     gate_tmp, cand_path)
    env = dict(os.environ)
    env.pop("CREW_COSMOS_BINDINGS", None)          # привязки — из дерева
    env["PYTHONUTF8"] = "1"
    env["CREW_TEMPLATE_CATALOG"] = str(gate_tmp / "catalog")
    root = Path(__file__).resolve().parents[1]
    for name in ("stand", "bindings_build", "bindings_gate", "candidate"):
        p = subprocess.run(cmds[name], cwd=str(root), env=env,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        assert p.returncode == 0, (name, cmds[name],
                                   (p.stdout or "")[-600:],
                                   (p.stderr or "")[-600:])
    stand = gate_tmp / "stand.json"
    assert stand.is_file(), "стенд не записал свидетельство"
    counts = crq._stand_counts(stand, "families/duty_limits")
    assert counts["passed"] and not counts["failed"], counts["rows"]
    assert counts["all"]["passed"] > counts["passed"], \
        "файл — свидетельство всего каталога, а не одной записи"
    assert not counts["all"]["failed"], counts["all"]["rows"]
    assert (crq._bindings_rows(gate_tmp / "bindings.json",
                               "families/duty_limits")
            .get("check_code", {}).get("state") == "есть")

    # Пятая команда — закон паритета (пакет «паритет на временном
    # каталоге», §2.1): argv судится ЗДЕСЬ, а исполнение настоящей
    # программой — соседним тестом (движок пишет дорожка А). Ключи и
    # порядок — дословно по контракту `catalog/tools/PARITY-CONTRACT.md`
    # §8: судимая папка — ВРЕМЕННЫЙ каталог попытки, привязки — её же,
    # вердикт — файлом.
    assert cmds["parity"][0] == sys.executable
    assert cmds["parity"][1] == str(copy_dir.parent / "catalog" / "tools"
                                    / "parity.py")
    assert cmds["parity"][2:] == [
        "--catalog", str(gate_tmp / "catalog"),
        "--candidate", str(cand_path),
        "--bindings", str(gate_tmp / "bindings.json"),
        "--json", str(gate_tmp / "parity.json")]

    # §7 п. 35 (пакет «Kotlin-исполнители агентом»): те же argv с адресом
    # эфемерного решателя. Стенд идёт на ВЕСЬ каталог (без --kinds), а
    # молчащий решатель — ОТКАЗ словами (код 1), а не ошибка употребления
    # ключей (код 2): виды решателя краснеют, чистые остаются зелёными.
    live = tmp_path / "gate-live"
    tc.materialize(copy_dir, tc.load_candidate(cand_path),
                   out_dir=live / "catalog")
    cmds_live = crq.catalog_gate_commands(sys.executable, copy_dir.parent,
                                          live, cand_path,
                                          "http://127.0.0.1:9")
    assert "--with-solver" in cmds_live["stand"]
    assert "--kinds" not in cmds_live["stand"]
    env_live = dict(env)
    env_live["CREW_TEMPLATE_CATALOG"] = str(live / "catalog")
    p = subprocess.run(cmds_live["stand"], cwd=str(root), env=env_live,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    assert p.returncode == 1, (p.stdout or "")[-600:]
    assert "http://127.0.0.1:9" in (p.stdout or "")
    counts_live = crq._stand_counts(live / "stand.json",
                                    "families/duty_limits")
    kot = counts_live["all"]["kinds"].get("kotlin_constraint") or {}
    assert kot.get("failed"), counts_live["all"]["rows"]
    assert not kot.get("skipped"), "с решателем виды решателя не пропускают"
    assert any("решатель" in str(r.get("note") or "")
               for r in counts_live["rows_full"])
    # красные строки стенда уходят в слова привязок, а не в код возврата
    for name in ("bindings_build", "bindings_gate", "candidate"):
        p2 = subprocess.run(cmds_live[name], cwd=str(root), env=env_live,
                            capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        assert p2.returncode == 0, (name, (p2.stdout or "")[-600:])

    # Закон паритета — НАСТОЯЩЕЙ программой на том же временном каталоге
    # (движок из дерева, а не из tmp-копии: копия его и не несёт —
    # `catalog_copy` пишет папку-черновик без `tools/`). Судится ровно
    # контракт: программа принимает argv гейта, пишет вердикт файлом,
    # код возврата дублируется полем `rc`, красный вердикт говорит
    # словами. Зелёное или красное содержание судят стенды дорожки А.
    law = root / "catalog" / "tools" / "parity.py"
    assert law.is_file(), (
        "закона паритета в дереве нет — после сведения пакета это отказ, "
        "а не пропуск: пятый гейт зовёт именно этот файл")
    p3 = subprocess.run([sys.executable, str(law), *cmds["parity"][2:]],
                        cwd=str(root), env=env, capture_output=True,
                        text=True, encoding="utf-8", errors="replace")
    assert p3.returncode in (0, 1), (
        "аргументы гейта закон не принял", cmds["parity"],
        (p3.stdout or "")[-600:], (p3.stderr or "")[-600:])
    verdict = json.loads((gate_tmp / "parity.json")
                         .read_text(encoding="utf-8"))
    assert set(verdict) >= {"rc", "ok", "rules", "catalog", "words"}
    assert verdict["rc"] == p3.returncode
    assert (verdict["ok"] is True) == (p3.returncode == 0)
    assert verdict["rules"], "вердикт без единого правила"
    if p3.returncode == 1:
        assert verdict["words"], "красный вердикт молчит"
    # Ключ `--candidate` не украшение: правило L21 «запись ≡ заявке»
    # обязано СУДИТЬ, а не отвечать «кандидатов рядом с каталогом нет».
    # Оно единственное сверяет запись с независимым якорем владельца, и
    # молчаливый пропуск делал бы `implemented` без единой такой сверки.
    assert verdict["rules"].get("L21", {}).get("ok") is not None, \
        "L21 пропущено: движку не отдали кандидата — запись судить не с чем"
    assert verdict["catalog"].get("with_candidates") == \
        ["families/duty_limits"], verdict["catalog"]
    # Шов «подделка ↔ движок»: вердикт, которым подделан `ev._run` в
    # остальных стендах, обязан быть той же формы, что вердикт настоящего
    # закона. Разойдись они — стенды конвейера учили бы читать поля,
    # которых движок не пишет (и наоборот).
    поддельный = _parity_json()
    assert set(поддельный) <= set(verdict) | {"growth_judged"}, \
        "подделка несёт поля, которых у движка нет"
    # Допуска здесь больше нет: подделка обязана быть подмножеством
    # НАСТОЯЩЕГО вердикта пятой команды. Прежнее исключение
    # (`source_fingerprint`, `with_candidates`) прятало то, что гейт с
    # тогдашним argv таких полей вообще не порождал, — стенды конвейера
    # учились читать кандидата там, где движок его не видел.
    assert set(поддельный["catalog"]) <= set(verdict["catalog"]), \
        sorted(set(поддельный["catalog"]) - set(verdict["catalog"]))
    assert set(поддельный["counts"]) == set(verdict["counts"])
    ключи = set().union(*(set(r) for r in verdict["rules"].values()))
    for имя, правило in поддельный["rules"].items():
        assert set(правило) <= ключи | {"words", "numbers", "skipped",
                                        "warning", "beyond_request"}, имя
    print(f"\nзакон гейта: код {p3.returncode}, правил "
          f"{len(verdict['rules'])}, форма подделки сошлась")


def test_закон_паритета_есть_в_дереве():
    """Сторож шва: пятый гейт CR и чек-лист выпуска зовут ФАЙЛ
    `catalog/tools/parity.py`. До сведения пакета его отсутствие было
    видимым пропуском (дорожка А ещё не сдана); после сведения это
    отказ — иначе живая половина контрактного теста выше замолчала бы
    вместе с движком."""
    from pathlib import Path
    law = (Path(__file__).resolve().parents[1] / "catalog" / "tools"
           / "parity.py")
    assert law.is_file(), f"закона паритета в дереве нет: {law}"
    assert "def judge(" in law.read_text(encoding="utf-8"), \
        "файл есть, а движка в нём нет"


def test_каталог_белый_список_путей(iso, catalog_repo, monkeypatch):
    repo, ev, _ = catalog_repo
    rq = _request()
    row = _catalog_cr(repo, rq)

    def effect():
        (repo / "webapp").mkdir(exist_ok=True)
        (repo / "webapp" / "x.py").write_text("x = 1\n", encoding="utf-8")
        return 0, "полез в чужое"
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(repo, ev, effect))
    out = crq.implement_cr(row["id"], ADMIN)
    assert out["status"] == "needs_human"
    assert "webapp/x.py" in out["impl"]["gates_report"]["paths"]

    crq._update_cr(row["id"], {"status": "accepted"})

    def effect2():
        (repo / "tests" / "test_run_button.py").write_text(
            "# правка чужого теста\n", encoding="utf-8")
        return 0, "поправил чужой тест"
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(repo, ev, effect2))
    out2 = crq.implement_cr(row["id"], ADMIN)
    assert out2["status"] == "needs_human"
    assert "существующий тест" in out2["impl"]["gates_report"]["paths"]


def test_каталог_исполнитель_вне_перечня_охраняемых_файлов(
        iso, catalog_repo, monkeypatch):
    repo, ev, _ = catalog_repo
    rq = _request()
    row = _catalog_cr(repo, rq)

    def effect():
        (repo / "vmp" / "bindings_seed.json").write_text(json.dumps(
            {"form_version": 1, "pending_records": [],
             "records": {"families/duty_limits": [
                 {"kind": "check_code", "state": "есть",
                  "file": "vmp/new_executor.py", "symbol": "f"}]}},
            ensure_ascii=False), encoding="utf-8")
        return 0, "новый файл под vmp/"
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(repo, ev, effect))
    out = crq.implement_cr(row["id"], ADMIN)
    assert out["status"] == "needs_human"
    assert "вне перечня охраняемых" in \
        out["impl"]["gates_report"]["executor_files"]


def test_каталог_предпосылки_клона_отказывают_до_квоты(
        iso, catalog_repo, monkeypatch):
    """Клон без кода каталога и дрейф отпечатка — отказ словами ДО ветки:
    квота дня не тратится."""
    repo, ev, _ = catalog_repo
    rq = _request()
    row = _catalog_cr(repo, rq)
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(
        repo, ev, lambda: (0, "не должен зваться")))
    # 1) дрейф отпечатка
    crq._update_cr(row["id"], {"record": {**row["record"],
                                          "catalog_fingerprint": "a" * 64}})
    with pytest.raises(crq.ChangeRequestError) as e:
        crq.implement_cr(row["id"], ADMIN)
    assert "не тот, по которому открыт CR" in str(e.value)
    assert crq._impl_quota_left() == crq.CR_IMPL_DAILY_LIMIT
    assert crq.get_cr(row["id"])["status"] == "accepted"
    # 2) клон без кода каталога
    crq._update_cr(row["id"], {"record": row["record"]})
    (repo / "template_catalog" / "__init__.py").unlink()
    (repo / "template_catalog").rmdir()
    for args in (["add", "-A"], ["commit", "-m", "срез без каталога"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True)
    with pytest.raises(crq.ChangeRequestError) as e2:
        crq.implement_cr(row["id"], ADMIN)
    assert "без кода каталога" in str(e2.value)
    assert crq._impl_quota_left() == crq.CR_IMPL_DAILY_LIMIT


def test_каталог_клон_без_origin_говорит_словами(
        iso, catalog_repo, monkeypatch):
    repo, ev, _ = catalog_repo
    rq = _request()
    row = _catalog_cr(repo, rq)

    def effect():
        (repo / "vmp" / "criteria.py").write_text("# 4\n", encoding="utf-8")
        _candidate_of(repo, rq, row["id"])
        return 0, "ок"
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(repo, ev, effect))
    out = crq.implement_cr(row["id"], ADMIN)
    assert out["impl"]["sync"] == "клон без origin: ветка от локального main"


# ------------------- каталог: исполнители решателя агентом (08.09.2026)
#
# Пакет «Kotlin-исполнители агентом»: дорожка «6-К» снята словом
# владельца, и недостающих судей добавил конвейер — сборка решателя из
# коммита ветки, тесты решателя после упаковки, эфемерный решатель,
# живой стенд по предмету и близнецам, регрессия числом.

_PROVIDER = (f"{_SOLVER_DIR}/FlightCrewSchedulingConstraintProvider.kt")
_TASK_MODES_PATH = f"{_SOLVER_DIR}/TaskModes.kt"
_NEW_TEST = f"{_SOLVER_TESTS}/solver/NewTest.kt"
#: Имя ограничения записи duty_limits в живом каталоге — предмет строки
#: посева и поле expected стендовых задач.
_CONSTRAINT = "Max duty period (rest-segmented)"


def _seed_of(repo, rows):
    """Строки посева записи, как их пишет агент."""
    (repo / "vmp" / "bindings_seed.json").write_text(json.dumps(
        {"form_version": 1, "pending_records": [],
         "records": {"families/duty_limits": list(rows)}},
        ensure_ascii=False), encoding="utf-8")


def _kotlin_seed(kind="kotlin_constraint", **over):
    """Строка посева вида решателя: файл — из пяти файлов гейта символов."""
    from vmp import bindings_gate as bg
    row = {"kind": kind, "state": "есть", "file": bg.PROVIDER_FILE,
           "symbol": "FlightCrewSchedulingConstraintProvider.maxDuty"}
    if kind == "kotlin_constraint":
        row["constraint"] = _CONSTRAINT
    if kind == "kotlin_knob":
        row["file"] = bg.RUN_THRESHOLDS_FILE
        row["symbol"] = "RunThresholds.KNOBS"
        row["param"] = "max_landings_per_duty"
    row.update(over)
    return row


def _copy_task(base_id, new_id, *, constraint=None, matches=None,
               amount=None, params=None):
    """Стендовая задача кандидата — копия живой задачи записи с иным
    ожиданием (числа берутся только из мира задачи)."""
    import copy

    from vmp import catalog_access as ca
    rec = ca.current().family("duty_limits")
    task = copy.deepcopy(next(t for t in rec["stand_tasks"]
                              if t["id"] == base_id))
    task["id"] = new_id
    kc = dict(task["expected"].get("kotlin_constraint") or {})
    if constraint is not None:
        kc["constraint"] = constraint
    if matches is not None:
        kc["matches"] = matches
    if amount is not None:
        kc["amount"] = amount
    task["expected"]["kotlin_constraint"] = kc
    if params is not None:
        task["world"]["params"] = dict(params)
    return task


def _kotlin_rows(pairs, constraint=_CONSTRAINT, kind="kotlin_constraint"):
    """Строки файла результатов стенда по видам решателя."""
    out = []
    for task, matches, result in pairs:
        expected = ({"constraint": constraint, "matches": matches,
                     "amount": matches}
                    if kind != "kotlin_metric"
                    else {"value": matches, "matches": matches,
                          "refusal": False})
        out.append(_stand_row(executor_kind=kind, task=task, result=result,
                              expected=expected, got=dict(expected),
                              run_id="run-kotlin"))
    return out


def _kotlin_effect(repo, rq, cid, *, seed=None, tasks=None, kinds=(),
                   also=None, test_text=None):
    """Работа агента по виду решателя: правка исполнителя в разрешённом
    файле, НОВЫЙ файл теста с литералом предмета, строки посева и
    кандидат со стендовыми задачами."""
    def effect():
        path = repo / _PROVIDER
        path.write_text(path.read_text(encoding="utf-8")
                        + "\nfun maxDuty() = 1\n", encoding="utf-8")
        new_test = repo / _NEW_TEST
        new_test.parent.mkdir(parents=True, exist_ok=True)
        new_test.write_text(test_text or
                            f'class NewTest {{ val c = "{_CONSTRAINT}" }}\n',
                            encoding="utf-8")
        _seed_of(repo, seed if seed is not None else [_kotlin_seed()])
        _candidate_of(repo, rq, cid, tasks=tasks, kinds=kinds)
        if also is not None:
            also()
        return 0, "исполнитель решателя дописан"
    return effect


def test_каталог_без_kotlin_видов_сборка_без_тестов_и_живой_стенд(
        iso, catalog_repo, monkeypatch, solver_stub):
    """§7 п. 20. У CR БЕЗ видов решателя решатель всё равно собирается
    (без тестов: исходники тождественны main) и поднимается: цепочка на
    живом каталоге и гейт временного каталога идут с `--with-solver`,
    иначе живые строки видов решателя стали бы `skipped` и свидетельство
    всего каталога понизилось бы."""
    repo, ev, _ = catalog_repo
    rq = _request()
    row = _catalog_cr(repo, rq)
    calls = []

    def effect():
        (repo / "vmp" / "criteria.py").write_text("# исполнитель 2\n",
                                                  encoding="utf-8")
        _candidate_of(repo, rq, row["id"])
        return 0, "дописал исполнителя"
    monkeypatch.setattr(ev, "_run",
                        _catalog_run_mock(repo, ev, effect, calls=calls))
    out = crq.implement_cr(row["id"], ADMIN)
    assert out["status"] == "implemented", out["impl"]["gates_report"]
    builds = [c for c in calls if "vmp.solver_build" in " ".join(c["cmd"])]
    assert len(builds) == 1 and "--with-tests" not in builds[0]["cmd"]
    assert "--source" in builds[0]["cmd"] and "commit" in builds[0]["cmd"]
    assert len(solver_stub["solvers"]) == 1
    assert solver_stub["solvers"][0].log == ["start", "ready", "stop"]
    stands = [c for c in calls if "vmp.stand_tasks" in " ".join(c["cmd"])]
    assert len(stands) == 2, [c["cmd"] for c in stands]
    for c in stands:
        assert "--with-solver" in c["cmd"]
        assert c["cmd"][c["cmd"].index("--with-solver") + 1] == \
            "http://127.0.0.1:1"
    solver = out["impl"]["gates_report"]["solver"]
    assert solver["class"] == "без правок решателя"
    assert solver["build"]["tests"] == crq.SOLVER_TESTS_SKIPPED
    # таблицы решателя у такого CR не снимаются — снимать нечего
    assert not [c for c in calls if "kotlin_tables" in " ".join(c["cmd"])]
    assert "kotlin-solver/" in row["work"]["forbidden_paths"]


def test_каталог_kotlin_порядок_цепочки(iso, catalog_repo, monkeypatch,
                                        solver_stub):
    """§7 п. 21. Порядок §1 у CR с видом решателя: снимок таблиц на main
    — агент — снимок на ветке — сборка с тестами — старт решателя —
    цепочка живого каталога — привязки — стенд временного каталога —
    привязки — кандидат — остановка — pytest — перечитывание. Набор клона
    идёт ПОСЛЕ гейтов и ПОСЛЕ остановки решателя (09.09.2026): набор
    упражняет гигиену процессов, и судья под ним не живёт."""
    repo, ev, _ = catalog_repo
    rq = _request(kinds=("kotlin_constraint",))
    row = _catalog_cr(repo, rq)
    calls = []
    tasks = [_copy_task("st-duty-1", "st-new-1", matches=1, amount=1),
             _copy_task("st-duty-1", "st-new-0", matches=0, amount=0)]
    rows = _kotlin_rows([("st-new-1", 1, "passed"),
                         ("st-new-0", 0, "passed")])
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(
        repo, ev, _kotlin_effect(repo, rq, row["id"], tasks=tasks,
                                 kinds=("kotlin_constraint",)),
        rows=rows, calls=calls,
        bindings=[{"kind": "kotlin_constraint", "state": "есть",
                   "file": _PROVIDER,
                   "symbol": "FlightCrewSchedulingConstraintProvider.maxDuty",
                   "cr_key": None}]))
    out = crq.implement_cr(row["id"], ADMIN)
    assert out["status"] == "implemented", out["impl"]["gates_report"]
    names = []
    for c in calls:
        joined = " ".join(c["cmd"])
        if c["cmd"][0] == "git":
            continue
        if "kotlin_tables" in joined:
            names.append("tables")
        elif c["cmd"][0] == ev.claude_cli():
            names.append("cli")
        elif "vmp.solver_build" in joined:
            names.append("build")
        elif "vmp.stand_tasks" in joined:
            names.append("stand-catalog" if "--catalog" in c["cmd"]
                         else "stand-live")
        elif "vmp.bindings_build" in joined:
            names.append("bindings_build")
        elif "vmp.bindings_gate" in joined:
            names.append("bindings_gate")
        elif "template_catalog.candidates" in joined:
            names.append("candidates")
        elif "pytest" in c["cmd"]:       # именно аргумент, а не путь tmp
            names.append("pytest")
    assert names == ["tables", "cli", "tables", "build", "stand-live",
                     "bindings_build", "bindings_gate",
                     "stand-catalog", "bindings_build", "bindings_gate",
                     "candidates", "pytest"], names
    # Остановка решателя — ДО набора: журнал подставного решателя закрыт
    # словом «stop» раньше вызова pytest.
    stop_at = next(i for i, c in enumerate(calls)
                   if "pytest" in c["cmd"])
    assert solver_stub["solvers"][0].log == ["start", "ready", "stop"]
    assert stop_at > 0
    builds = [c for c in calls if "vmp.solver_build" in " ".join(c["cmd"])]
    assert "--with-tests" in builds[0]["cmd"]
    solver = out["impl"]["gates_report"]["solver"]
    assert solver["build"]["rc_test"] == 0
    assert solver["class"] == "К-исп"
    assert solver_stub["solvers"][0].log == ["start", "ready", "stop"]
    # выжимка для карточки строки (0.16): класс, коды, отпечаток, адрес
    card = out["impl"]["solver"]
    assert card["class"] == "К-исп" and card["build_rc"] == 0
    assert card["app_jar_sha256"] == "a" * 12
    assert card["url"] == "http://127.0.0.1:1"
    assert card["stand_by_kind"]["kotlin_constraint"]["passed"] == 2
    n = subprocess.run(["git", "-C", str(repo), "rev-list", "--count",
                        "main..cr-" + row["id"]], capture_output=True,
                       text=True, encoding="utf-8",
                       errors="replace").stdout.strip()
    assert n == "2"


def test_каталог_kotlin_предпосылка_словами_до_квоты(
        iso, catalog_repo, monkeypatch, solver_stub):
    """§7 п. 22. Проба тулчейна отказывает — слова, ветки нет, квота дня
    цела, строка осталась принятой.

    Проба идёт у КАЖДОГО CR каталога, а не только у CR с видами
    решателя: сборка и эфемерный решатель нужны каждому (0.2), и
    `implemented` требует их у каждого (0.16) — держать пробу за
    kotlin-видами значило бы на контуре без Java прогнать агента
    полчаса, взять реализацию дня и лишь на шаге сборки сказать
    «сборка решателя недоступна» вопреки условию 0.12 («сборка
    недоступна» звучит словами ДО траты квоты). Снимок таблиц остаётся
    за kotlin-видами: класс правки нужен только им."""
    repo, ev, _ = catalog_repo
    rq = _request(kinds=("kotlin_constraint",))
    row = _catalog_cr(repo, rq)
    solver_stub["probe"] = {
        "ok": False,
        "words": "Java не найдена (искали: COSMOS_SOLVER_JAVA_HOME, "
                 "JAVA_HOME, <клон>/.tools/jdk*, PATH)"}
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(
        repo, ev, lambda: (0, "не должен зваться")))
    with pytest.raises(crq.ChangeRequestError) as e:
        crq.implement_cr(row["id"], ADMIN)
    assert "сборка решателя на этом контуре недоступна" in str(e.value)
    assert "Java не найдена" in str(e.value)
    assert crq._impl_quota_left() == crq.CR_IMPL_DAILY_LIMIT
    assert crq.get_cr(row["id"])["status"] == "accepted"
    branches = subprocess.run(["git", "-C", str(repo), "branch"],
                              capture_output=True, text=True,
                              encoding="utf-8").stdout
    assert "cr-" + row["id"] not in branches
    # У CR БЕЗ видов решателя проба тоже зовётся — и тоже ДО квоты.
    solver_stub["probe"] = {"ok": False,
                            "words": "кэш Maven неполон: нет surefire"}
    rq2 = _request()
    row2 = _catalog_cr(repo, rq2)
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(
        repo, ev, lambda: (0, "не должен зваться")))
    with pytest.raises(crq.ChangeRequestError) as e2:
        crq.implement_cr(row2["id"], ADMIN)
    assert "кэш Maven неполон" in str(e2.value)
    assert crq._impl_quota_left() == crq.CR_IMPL_DAILY_LIMIT
    assert crq.get_cr(row2["id"])["status"] == "accepted"
    assert "cr-" + row2["id"] not in subprocess.run(
        ["git", "-C", str(repo), "branch"], capture_output=True, text=True,
        encoding="utf-8").stdout
    # ... а снимок таблиц решателя у него не снимается: класс правки
    # нужен только видам решателя.
    solver_stub["probe"] = {"ok": True, "words": "проба тулчейна: всё"}
    calls = []

    def effect():
        (repo / "vmp" / "criteria.py").write_text("# 9\n", encoding="utf-8")
        _candidate_of(repo, rq2, row2["id"])
        return 0, "ок"
    monkeypatch.setattr(ev, "_run",
                        _catalog_run_mock(repo, ev, effect, calls=calls))
    assert crq.implement_cr(row2["id"], ADMIN)["status"] == "implemented"
    assert not [c for c in calls if "kotlin_tables" in " ".join(c["cmd"])]
    # Сборщик сирот зовётся меткой СВОЕГО контура, а не всех эфемерных
    # решателей машины: контуры стоят на одной машине под одной записью.
    assert solver_stub["orphan_markers"], "сборщик сирот не звался"
    assert all(m.startswith("-Dcosmos.ephemeral=конт7:")
               for m in solver_stub["orphan_markers"]), \
        solver_stub["orphan_markers"]


def test_каталог_kotlin_красная_сборка_needs_human_и_работа_сохранена(
        iso, catalog_repo, monkeypatch, solver_stub):
    """§7 п. 23. Красная сборка: решатель не поднимается, живой прогон
    НЕ пропускается молча (у видов решателя — слова), работа агента
    остаётся в ветке двумя коммитами, хвост журнала сборки — в карточке."""
    repo, ev, _ = catalog_repo
    rq = _request(kinds=("kotlin_constraint",))
    row = _catalog_cr(repo, rq)
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(
        repo, ev, _kotlin_effect(repo, rq, row["id"],
                                 kinds=("kotlin_constraint",)),
        build=_build_json(rc_test=1, ok=False,
                          surefire={"tests": 368, "failures": 2,
                                    "errors": 0, "skipped": 1},
                          log_tail="Tests run: 368, Failures: 2")))
    out = crq.implement_cr(row["id"], ADMIN)
    assert out["status"] == "needs_human"
    solver = out["impl"]["gates_report"]["solver"]
    assert "код 1" in solver["build"]["words"]
    assert "failures 2" in solver["build"]["words"]
    assert solver_stub["solvers"] == []          # решатель не поднимался
    assert "живой прогон не состоялся: сборка красная" in \
        solver["stand"]["words"]
    assert "Failures: 2" in out["impl"]["build_tail"]
    n = subprocess.run(["git", "-C", str(repo), "rev-list", "--count",
                        "main..cr-" + row["id"]], capture_output=True,
                       text=True, encoding="utf-8",
                       errors="replace").stdout.strip()
    assert n == "2"


def test_каталог_kotlin_решатель_останавливается_при_исключении_гейта(
        iso, catalog_repo, monkeypatch, solver_stub):
    """§7 п. 24. Исключение между стартом решателя и свидетельством
    проходит через остановку JVM: сирот после красного прогона нет."""
    repo, ev, _ = catalog_repo
    rq = _request(kinds=("kotlin_constraint",))
    row = _catalog_cr(repo, rq)
    base = _catalog_run_mock(repo, ev, _kotlin_effect(
        repo, rq, row["id"], kinds=("kotlin_constraint",)))

    def boom(cmd, cwd, timeout_s, **kw):
        if "template_catalog.candidates" in " ".join(str(c) for c in cmd):
            raise RuntimeError("проверка кандидата упала")
        return base(cmd, cwd, timeout_s, **kw)
    monkeypatch.setattr(ev, "_run", boom)
    with pytest.raises(crq.ChangeRequestError):
        crq.implement_cr(row["id"], ADMIN)
    assert solver_stub["solvers"][0].stopped is True
    got = crq.get_cr(row["id"])
    assert got["status"] == "needs_human"
    assert "internal error" in got["impl"]["report"]
    st = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                        capture_output=True, text=True,
                        encoding="utf-8", errors="replace").stdout.strip()
    assert st == ""
    cur = subprocess.run(["git", "-C", str(repo), "rev-parse",
                          "--abbrev-ref", "HEAD"], capture_output=True,
                         text=True, encoding="utf-8").stdout.strip()
    assert cur == "main"


def test_каталог_kotlin_белый_список_и_судьи_kotlin(
        iso, catalog_repo, monkeypatch, solver_stub):
    """§7 п. 25. Шесть красных путей и один зелёный: инструмент
    измерения, сборка, конфигурация запуска, существующий тест решателя,
    правка `TaskModes.kt` вне блока метрик и чтение среды в добавленной
    строке — каждый со своими словами; правка исполнителя решателя с
    новым файлом теста проходит."""
    repo, ev, _ = catalog_repo
    rq = _request(kinds=("kotlin_constraint",))
    row = _catalog_cr(repo, rq)
    # семь нажатий подряд — суточная квота здесь не предмет суда
    monkeypatch.setattr(crq, "CR_IMPL_DAILY_LIMIT", 20)

    def touch(rel, text):
        def _do():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return _do
    rest = ("kotlin-solver/src/main/kotlin/org/acme/flighcrewscheduling/"
            "rest/FlightCrewSchedulingResource.kt")
    cases = [
        (rest, "package org.acme.flighcrewscheduling.rest\nclass Res2\n",
         "судьи, гейты и чужие зоны"),
        ("kotlin-solver/pom.xml", "<project><x/></project>\n",
         "под сторожем очереди"),
        ("kotlin-solver/src/main/resources/application.properties",
         "quarkus.http.port=1\n", "судьи, гейты и чужие зоны"),
        (f"{_SOLVER_TESTS}/solver/CrewSizeConstraintTest.kt",
         "package org.acme.flighcrewscheduling.solver\nclass T2\n",
         "существующий файл тестов решателя"),
        (_TASK_MODES_PATH, _TASK_MODES.replace("fun modeOf(name: String)",
                                               "fun modeOf(name: String) "),
         "правка вне METRIC_EXECUTORS"),
        (_PROVIDER, "package org.acme.flighcrewscheduling.solver\n"
                    "val home = System.getenv(\"HOME\")\n",
         "читает среду, диск или сеть"),
    ]
    for rel, text, words in cases:
        crq._update_cr(row["id"], {"status": "accepted"})
        monkeypatch.setattr(ev, "_run", _catalog_run_mock(
            repo, ev, _kotlin_effect(repo, rq, row["id"],
                                     kinds=("kotlin_constraint",),
                                     also=touch(rel, text))))
        out = crq.implement_cr(row["id"], ADMIN)
        assert out["status"] == "needs_human", (rel, out["impl"])
        assert words in out["impl"]["gates_report"]["paths"], (rel, out)
    # зелёный путь: исполнитель решателя и новый файл теста
    crq._update_cr(row["id"], {"status": "accepted"})
    tasks = [_copy_task("st-duty-1", "st-new-1", matches=1, amount=1),
             _copy_task("st-duty-1", "st-new-0", matches=0, amount=0)]
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(
        repo, ev, _kotlin_effect(repo, rq, row["id"], tasks=tasks,
                                 kinds=("kotlin_constraint",)),
        rows=_kotlin_rows([("st-new-1", 1, "passed"),
                           ("st-new-0", 0, "passed")]),
        bindings=[{"kind": "kotlin_constraint", "state": "есть",
                   "file": _PROVIDER, "symbol": "P.maxDuty",
                   "cr_key": None}]))
    out = crq.implement_cr(row["id"], ADMIN)
    assert out["status"] == "implemented", out["impl"]["gates_report"]


def test_каталог_kotlin_посев_только_файлы_гейта_символов_и_без_судей(
        iso, catalog_repo, monkeypatch, solver_stub):
    """§7 п. 26. Строка посева вида решателя указывает на один из пяти
    файлов гейта символов; помощник под `solver/` — «вне перечня»; а
    файл-СУДЬЯ (живой прогон стенда) исполнителем записи объявлен быть
    не может — он в CODE_FILES, и прежде проходил как «исполнитель»."""
    repo, ev, _ = catalog_repo
    rq = _request(kinds=("kotlin_constraint",))
    row = _catalog_cr(repo, rq)
    tasks = [_copy_task("st-duty-1", "st-new-1", matches=1, amount=1),
             _copy_task("st-duty-1", "st-new-0", matches=0, amount=0)]
    green = _catalog_run_mock(
        repo, ev, _kotlin_effect(repo, rq, row["id"], tasks=tasks,
                                 kinds=("kotlin_constraint",)),
        rows=_kotlin_rows([("st-new-1", 1, "passed"),
                           ("st-new-0", 0, "passed")]),
        bindings=[{"kind": "kotlin_constraint", "state": "есть",
                   "file": _PROVIDER, "symbol": "P.maxDuty",
                   "cr_key": None}])
    monkeypatch.setattr(ev, "_run", green)
    assert crq.implement_cr(row["id"], ADMIN)["status"] == "implemented"
    # помощник под solver/ — не файл гейта символов
    crq._update_cr(row["id"], {"status": "accepted"})
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(
        repo, ev, _kotlin_effect(
            repo, rq, row["id"], kinds=("kotlin_constraint",),
            seed=[_kotlin_seed(file=f"{_SOLVER_DIR}/Helper.kt")])))
    out = crq.implement_cr(row["id"], ADMIN)
    assert out["status"] == "needs_human"
    assert "вне перечня гейта символов" in \
        out["impl"]["gates_report"]["executor_files"]
    # судья вместо исполнителя
    crq._update_cr(row["id"], {"status": "accepted"})
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(
        repo, ev, _kotlin_effect(
            repo, rq, row["id"], kinds=("kotlin_constraint",),
            seed=[{"kind": "check_code", "state": "есть",
                   "file": "vmp/stand_solver.py",
                   "symbol": "constraint_row"}])))
    out2 = crq.implement_cr(row["id"], ADMIN)
    assert out2["status"] == "needs_human"
    assert "это судья, а не исполнитель" in \
        out2["impl"]["gates_report"]["executor_files"]


def test_каталог_kotlin_суд_по_предмету_и_различающим_задачам(
        iso, catalog_repo, monkeypatch, solver_stub):
    """§7 п. 27. Зелёная задача ЧУЖОГО ограничения того же вида
    исполнителя не судит; две задачи предмета с одинаковым числом
    срабатываний — тоже (всегда-истинное ограничение с ожиданием,
    списанным с его вывода, прошло бы); зелено — когда есть ноль и
    ненулевое."""
    repo, ev, _ = catalog_repo
    rq = _request(kinds=("kotlin_constraint",))
    row = _catalog_cr(repo, rq)
    tasks = [_copy_task("st-duty-1", "st-new-1", matches=1, amount=1),
             _copy_task("st-duty-1", "st-new-0", matches=0, amount=0)]
    binds = [{"kind": "kotlin_constraint", "state": "есть",
              "file": _PROVIDER, "symbol": "P.maxDuty", "cr_key": None}]

    def run(rows):
        return _catalog_run_mock(
            repo, ev, _kotlin_effect(repo, rq, row["id"], tasks=tasks,
                                     kinds=("kotlin_constraint",)),
            rows=rows, bindings=binds)
    # 1) чужой предмет
    monkeypatch.setattr(ev, "_run", run(_kotlin_rows(
        [("st-new-1", 1, "passed")], constraint="Чужое ограничение")))
    out = crq.implement_cr(row["id"], ADMIN)
    assert out["status"] == "needs_human"
    assert "нет задач по предмету" in \
        out["impl"]["gates_report"]["solver"]["stand"]["words"]
    # 2) две задачи с одинаковым ожиданием
    crq._update_cr(row["id"], {"status": "accepted"})
    monkeypatch.setattr(ev, "_run", run(_kotlin_rows(
        [("st-new-1", 1, "passed"), ("st-new-0", 1, "passed")])))
    out2 = crq.implement_cr(row["id"], ADMIN)
    assert out2["status"] == "needs_human"
    assert "нет различающих ожиданий" in \
        out2["impl"]["gates_report"]["solver"]["stand"]["words"]
    # 3) ноль и ненулевое — зелено
    crq._update_cr(row["id"], {"status": "accepted"})
    monkeypatch.setattr(ev, "_run", run(_kotlin_rows(
        [("st-new-1", 1, "passed"), ("st-new-0", 0, "passed")])))
    out3 = crq.implement_cr(row["id"], ADMIN)
    assert out3["status"] == "implemented", out3["impl"]["gates_report"]
    subject = out3["impl"]["gates_report"]["solver"]["stand"]["by_subject"]
    assert f"kotlin_constraint:{_CONSTRAINT}" in subject
    assert sorted(subject[f"kotlin_constraint:{_CONSTRAINT}"]
                  ["matches_values"]) == [0, 1]


def test_каталог_kotlin_knob_близнецы(iso, catalog_repo, monkeypatch,
                                      solver_stub):
    """§7 п. 28. Ручка судится ПАРОЙ ЗАДАЧ-БЛИЗНЕЦОВ: миры отличаются
    ровно значением её параметра, ожидания — числом срабатываний. Мир,
    отличающийся двумя полями, — красное словами: гейт символов
    доказывает лишь существование метода."""
    repo, ev, _ = catalog_repo
    rq = _request(kinds=("kotlin_knob",))
    row = _catalog_cr(repo, rq)
    binds = [{"kind": "kotlin_knob", "state": "есть",
              "file": f"{_SOLVER_DIR}/RunThresholds.kt",
              "symbol": "RunThresholds.KNOBS", "cr_key": None}]
    rows = _kotlin_rows([("st-twin-a", 1, "passed"),
                         ("st-twin-b", 0, "passed")])

    def run(tasks):
        return _catalog_run_mock(
            repo, ev, _kotlin_effect(repo, rq, row["id"], tasks=tasks,
                                     kinds=("kotlin_knob",),
                                     seed=[_kotlin_seed("kotlin_knob")]),
            rows=rows, bindings=binds)
    twin_a = _copy_task("st-duty-2", "st-twin-a", matches=1, amount=1,
                        params={"max_duty_duration": 780,
                                "max_landings_per_duty": 1})
    twin_b = _copy_task("st-duty-2", "st-twin-b", matches=0, amount=0,
                        params={"max_duty_duration": 780,
                                "max_landings_per_duty": 9})
    monkeypatch.setattr(ev, "_run", run([twin_a, twin_b]))
    out = crq.implement_cr(row["id"], ADMIN)
    assert out["status"] == "implemented", out["impl"]["gates_report"]
    twins = out["impl"]["gates_report"]["solver"]["stand"]["twins"]
    assert twins and twins[0]["field"] == "params.max_landings_per_duty"
    assert sorted(twins[0]["matches"]) == [0, 1]
    # миры отличаются двумя полями — пары нет
    crq._update_cr(row["id"], {"status": "accepted"})
    twin_c = _copy_task("st-duty-2", "st-twin-b", matches=0, amount=0,
                        params={"max_duty_duration": 999,
                                "max_landings_per_duty": 9})
    monkeypatch.setattr(ev, "_run", run([twin_a, twin_c]))
    out2 = crq.implement_cr(row["id"], ADMIN)
    assert out2["status"] == "needs_human"
    words = out2["impl"]["gates_report"]["solver"]["stand"]["words"]
    assert "пары задач-близнецов нет" in words
    assert "params.max_landings_per_duty" in words


def test_каталог_регрессия_числом_и_без_понижения(
        iso, catalog_repo, monkeypatch, solver_stub):
    """§7 п. 29. Регрессия по соседям считается ЧИСЛОМ: зелёных строк
    вида решателя не меньше, чем на main; судимая на main строка,
    ставшая `skipped`, — красное; строка с пометкой `KNOWN_FAILED:`
    идёт отдельным счётчиком, а не в красные."""
    repo, ev, _ = catalog_repo
    rq = _request(kinds=("kotlin_constraint",))
    row = _catalog_cr(repo, rq)
    main_rows = ([{"record": "families/other", "executor_kind":
                   "kotlin_constraint", "task": f"st-o-{i}",
                   "result": "passed"} for i in range(12)]
                 + [{"record": "families/x", "executor_kind": "check_code",
                     "task": "st-x-1", "result": "passed"}])
    (repo / "vmp" / "stand_results.json").write_text(
        json.dumps({"form_version": 1, "results": main_rows},
                   ensure_ascii=False), encoding="utf-8")
    for args in (["add", "-A"], ["commit", "-m", "мера регрессии"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True)
    tasks = [_copy_task("st-duty-1", "st-new-1", matches=1, amount=1),
             _copy_task("st-duty-1", "st-new-0", matches=0, amount=0)]
    binds = [{"kind": "kotlin_constraint", "state": "есть",
              "file": _PROVIDER, "symbol": "P.maxDuty", "cr_key": None}]
    mine = _kotlin_rows([("st-new-1", 1, "passed"),
                         ("st-new-0", 0, "passed")])
    def others(n, extra=()):
        return [_stand_row(record="families/other",
                           executor_kind="kotlin_constraint",
                           task=f"st-o-{i}", result="passed")
                for i in range(n)] + list(extra)

    def run(rows):
        return _catalog_run_mock(
            repo, ev, _kotlin_effect(repo, rq, row["id"], tasks=tasks,
                                     kinds=("kotlin_constraint",)),
            rows=rows, bindings=binds)
    # девять соседей плюс две мои строки — одиннадцать против двенадцати
    monkeypatch.setattr(ev, "_run", run(mine + others(9)))
    out = crq.implement_cr(row["id"], ADMIN)
    assert out["status"] == "needs_human"
    stand = out["impl"]["gates_report"]["solver"]["stand"]
    assert "против 12 на main" in stand["words"]
    assert stand["main_by_kind"]["kotlin_constraint"] == 12
    # понижение судимой строки до skipped (числом при этом всё сходится)
    crq._update_cr(row["id"], {"status": "accepted"})
    dropped = others(11, [_stand_row(record="families/other",
                                     executor_kind="kotlin_constraint",
                                     task="st-o-11", result="skipped")])
    monkeypatch.setattr(ev, "_run", run(mine + dropped))
    out2 = crq.implement_cr(row["id"], ADMIN)
    assert out2["status"] == "needs_human"
    stand2 = out2["impl"]["gates_report"]["solver"]["stand"]
    assert "понижение до skipped" in stand2["words"]
    assert stand2["downgraded"] == 1
    # известная красная строка — отдельным счётчиком: запись владельца
    # гейт не разблокирует молча и второй раз его не краснит
    crq._update_cr(row["id"], {"status": "accepted"})
    known = others(11, [_stand_row(
        record="families/other", executor_kind="kotlin_constraint",
        task="st-o-11", result="failed",
        note="получено 0/0; KNOWN_FAILED: разобрано словом владельца")])
    monkeypatch.setattr(ev, "_run", run(mine + known))
    out3 = crq.implement_cr(row["id"], ADMIN)
    stand3 = out3["impl"]["gates_report"]["solver"]["stand"]
    assert stand3["known_failed"] == 1
    assert out3["impl"]["gates_report"]["stand"]["all"]["failed"] == 0
    assert "по прочим записям" not in \
        (out3["impl"]["gates_report"]["stand"].get("words") or "")
    assert out3["status"] == "implemented", out3["impl"]["gates_report"]


def _к_табл_прогон(repo, ev, monkeypatch, solver_stub, rq, row, *,
                   parity=None):
    """Прогон CR класса К-табл: таблицы решателя выросли на ручку.

    Общее тело трёх стендов пакета «паритет на временном каталоге»
    (§5): исход решает ВЕРДИКТ ЗАКОНА, а не класс правки."""
    solver_stub["growth"] = {**_NO_GROWTH,
                             "knobs": ["max_sectors_per_duty"]}
    tasks = [_copy_task("st-duty-1", "st-new-1", matches=1, amount=1),
             _copy_task("st-duty-1", "st-new-0", matches=0, amount=0)]
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(
        repo, ev, _kotlin_effect(repo, rq, row["id"], tasks=tasks,
                                 kinds=("kotlin_constraint",)),
        rows=_kotlin_rows([("st-new-1", 1, "passed"),
                           ("st-new-0", 0, "passed")]),
        bindings=[{"kind": "kotlin_constraint", "state": "есть",
                   "file": _PROVIDER, "symbol": "P.maxDuty",
                   "cr_key": None}],
        parity=parity))
    return crq.implement_cr(row["id"], ADMIN)


def test_каталог_к_табл_доходит_до_implemented_при_зелёном_паритете(
        iso, catalog_repo, monkeypatch, solver_stub):
    """§5 пакета «паритет на временном каталоге» (взамен §7 п. 30 пакета
    «Kotlin-исполнители»). Рост таблиц решателя, ОБЪЯВЛЕННЫЙ записью
    кандидата, доходит до `implemented`: класс правки К-табл, вердикт
    закона на временном каталоге зелёный, `pytest` дерева зелёный (он
    судит «живой ⊕ кандидаты», ключей `--ignore` у него нет).

    Прежде эта же строка кончалась `needs_human` по построению — слово
    владельца требует, чтобы `needs_human` был честным красным, а не
    маршрутом."""
    repo, ev, _ = catalog_repo
    rq = _request(kinds=("kotlin_constraint",))
    row = _catalog_cr(repo, rq)
    out = _к_табл_прогон(repo, ev, monkeypatch, solver_stub, rq, row)
    report = out["impl"]["gates_report"]
    assert out["status"] == "implemented", report
    solver = report["solver"]
    assert solver["class"] == "К-табл"
    assert solver["tables_growth"]["knobs"] == ["max_sectors_per_duty"]
    # поле класса осталось ссылкой словами, а не судьёй и не отказом
    assert "max_sectors_per_duty" in solver["parity"]
    assert "gates.parity" in solver["parity"]
    assert not solver["words"], solver["words"]
    # вердикт закона — КОПИЯ файла движка, а не пересказ конвейером
    assert report["parity"]["ok"] is True and report["parity"]["rc"] == 0
    assert report["parity"]["rules"]["L05"]["numbers"]["params"] == 47
    assert report["parity"]["catalog"]["version"] == "0.2.1-draft.1"
    # отчёт строки называет рост словами и обе половины судьбы паритета
    words = out["impl"]["report"]
    assert "max_sectors_per_duty" in words
    assert "объявлен записью кандидата" in words
    assert "после сборки записи службой" in words
    # свидетельство кандидата несёт тот же вердикт (блок gates открыт)
    shown = subprocess.run(
        ["git", "-C", str(repo), "show",
         "cr-" + row["id"] + ":catalog/candidates/families/duty_limits.json"],
        capture_output=True, text=True, encoding="utf-8")
    ev_doc = json.loads(shown.stdout)
    parity_ev = ev_doc["evidence"]["gates"]["parity"]
    assert parity_ev["ok"] is True
    # Путь машины в свидетельство не едет: блок паритета идёт через тот
    # же скруббер, что и остальные гейты (§2.4 проекта). Судится на
    # настоящем пути судимой папки, который подделка кладёт в вердикт.
    assert str(repo) not in json.dumps(parity_ev, ensure_ascii=False), \
        f"путь машины уехал в свидетельство: {parity_ev['catalog']['path']}"


def test_каталог_красный_паритет_словами(
        iso, catalog_repo, monkeypatch, solver_stub):
    """§5. Красный вердикт закона — `needs_human` СЛОВАМИ ПРАВИЛА, а не
    молчанием: рост, которого запись кандидата не объявила, конвейер
    пропустить не вправе, даже когда сборка, стенд и pytest зелены."""
    repo, ev, _ = catalog_repo
    rq = _request(kinds=("kotlin_constraint",))
    row = _catalog_cr(repo, rq)
    красный = _parity_json(
        rc=1, ok=False,
        counts={"rules": 23, "green": 21, "red": 1, "skipped": 1,
                "warning": 0},
        rules={"L05": {"ok": False,
                       "words": "ручка решателя max_sectors_per_duty "
                                "(count, max) — параметра нет ни в записи "
                                "кандидата, ни в словаре"}},
        words="L05: ручка решателя max_sectors_per_duty (count, max) — "
              "параметра нет ни в записи кандидата, ни в словаре")
    out = _к_табл_прогон(repo, ev, monkeypatch, solver_stub, rq, row,
                         parity=красный)
    report = out["impl"]["gates_report"]
    assert out["status"] == "needs_human"
    assert report["parity"]["ok"] is False and report["parity"]["rc"] == 1
    assert "max_sectors_per_duty" in report["parity"]["words"]
    assert report["parity"]["rules"]["L05"]["ok"] is False
    # сборка, стенд и pytest при этом зелёные — красен именно закон
    assert not report["solver"]["build"].get("words")
    assert not report["solver"]["stand"].get("words")
    assert report["pytest"] == "green"
    assert "паритет на временном каталоге красен" in out["impl"]["report"]
    assert "max_sectors_per_duty" in out["impl"]["report"]


def test_каталог_слов_по_построению_больше_нет():
    """§5. В конвейере не осталось ни слов «красен по построению», ни
    константы `_PARITY_TESTS`, ни разбора красных тестов паритета: суд
    по построению — это отказ судить, и слово владельца его сняло."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for name in ("webapp/change_requests.py", "webapp/catalog_pipeline.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "красен по построению" not in text, name
        assert "красны по построению до" not in text, name
        assert "_PARITY_TESTS" not in text, name
    assert not hasattr(crq, "_PARITY_TESTS")
    assert not hasattr(crq, "_pytest_failures")
    # `pytest` клона судит «живой ⊕ кандидаты» и судит ВСЁ: перечня
    # пропусков у него нет и появиться не должно (0.3).
    text = (root / "webapp/change_requests.py").read_text(encoding="utf-8")
    assert '[py, "-m", "pytest", "-q"]' in text
    assert '"--ignore' not in text


def test_каталог_вердикт_паритета_копия_и_сходится_с_кодом(tmp_path):
    """§5. Своего суждения о паритете у конвейера нет: вердикт — копия
    файла движка. Но связка «код возврата ↔ вердикт» судится: нет файла,
    он не разобран или вердикт спорит с кодом — красное словами."""
    green = tmp_path / "parity.json"
    green.write_text(json.dumps(_parity_json(), ensure_ascii=False),
                     encoding="utf-8")
    doc = crq._parity_verdict(green, 0, "")
    assert doc["ok"] is True and doc["rules"]["L05"]["numbers"]["params"] == 47

    # файла нет — «не прогонялся» зелёным не притворяется
    нет = crq._parity_verdict(tmp_path / "нет.json", 2, "трассировка")
    assert нет["ok"] is False and нет["rc"] == 2
    assert "не прочитан" in нет["words"] and "трассировка" in нет["words"]

    # мусор вместо вердикта
    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    assert crq._parity_verdict(bad, 0, "")["ok"] is False

    # вердикт зелен, а команда вернула 1 — верим коду возврата
    спор = tmp_path / "спор.json"
    спор.write_text(json.dumps(_parity_json(), ensure_ascii=False),
                    encoding="utf-8")
    doc2 = crq._parity_verdict(спор, 1, "")
    assert doc2["ok"] is False
    assert "не сходится с кодом возврата" in doc2["words"]


def test_каталог_kotlin_свидетельство_без_машинных_путей_с_личностью(
        iso, catalog_repo, monkeypatch, solver_stub):
    """§7 п. 31. В свидетельстве кандидата нет машинных путей клона и
    попытки, есть личность артефакта четырьмя хешами, строки живого
    стенда с номером прогона и адресом, а `executors` несут файл и
    символ — без них служба отказалась бы принять запись."""
    repo, ev, _ = catalog_repo
    rq = _request(kinds=("kotlin_constraint",))
    row = _catalog_cr(repo, rq)
    tasks = [_copy_task("st-duty-1", "st-new-1", matches=1, amount=1),
             _copy_task("st-duty-1", "st-new-0", matches=0, amount=0)]
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(
        repo, ev, _kotlin_effect(repo, rq, row["id"], tasks=tasks,
                                 kinds=("kotlin_constraint",)),
        rows=_kotlin_rows([("st-new-1", 1, "passed"),
                           ("st-new-0", 0, "passed")]),
        bindings=[{"kind": "kotlin_constraint", "state": "есть",
                   "file": _PROVIDER, "symbol": "P.maxDuty",
                   "cr_key": None}]))
    out = crq.implement_cr(row["id"], ADMIN)
    assert out["status"] == "implemented", out["impl"]["gates_report"]
    shown = subprocess.run(
        ["git", "-C", str(repo), "show",
         "cr-" + row["id"] + ":catalog/candidates/families/duty_limits.json"],
        capture_output=True, text=True, encoding="utf-8").stdout
    doc = json.loads(shown)
    ev_doc = doc["evidence"]
    text = json.dumps(ev_doc, ensure_ascii=False)
    assert str(repo) not in text and str(repo).replace("\\", "/") not in text
    assert "attempt-1\\" not in text
    assert not re.search(r"(?<![A-Za-z])[A-Za-z]:[\\/]", text), text[:400]
    art = ev_doc["gates"]["solver"]["build"]["artifact"]
    assert all(art[k] for k in ("app_jar_sha256", "run_jar_sha256",
                                "dependencies_txt_sha256", "tree_sha256"))
    rows = ev_doc["gates"]["solver"]["stand"]["rows"]
    assert rows and rows[0]["run_id"] == "run-kotlin"
    assert ev_doc["executors"][0]["file"] and ev_doc["executors"][0]["symbol"]
    assert "tmp" not in ev_doc["gates"]
    assert ev_doc["gates"]["solver"]["run"]["url"] == "http://127.0.0.1:1"
    # кандидата из ветки принимает тот же читатель, что и служба
    from template_catalog import candidates as tc
    path = repo / "catalog" / "candidates" / "families" / "duty_limits.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(shown, encoding="utf-8")
    assert tc.load_candidate(path)["cr_key"] == row["id"]
    path.unlink()
    assert out["impl"]["gates_report"]["candidate_reload"] == "ok"


def test_каталог_попытки_не_смешиваются(iso, catalog_repo, monkeypatch,
                                        solver_stub):
    """§7 п. 32. Папка попытки своя у каждого нажатия: свидетельство
    второй попытки читается из своей папки, а не из прошлой."""
    repo, ev, _ = catalog_repo
    rq = _request()
    row = _catalog_cr(repo, rq)

    def effect():
        (repo / "vmp" / "criteria.py").write_text("# ещё раз\n",
                                                  encoding="utf-8")
        _candidate_of(repo, rq, row["id"])
        return 0, "ок"
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(repo, ev, effect))
    first = crq.implement_cr(row["id"], ADMIN)
    assert first["impl"]["gates_report"]["solver"]["attempt"] == 1
    crq._update_cr(row["id"], {"status": "accepted"})
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(
        repo, ev, effect, stand_result="failed"))
    second = crq.implement_cr(row["id"], ADMIN)
    assert second["impl"]["gates_report"]["solver"]["attempt"] == 2
    assert second["impl"]["gates_report"]["stand"]["failed"] == 1
    root = repo / "_repro" / f"catalog-cr-{row['id']}"
    assert (root / "attempt-1" / "stand.json").is_file()
    assert (root / "attempt-2" / "stand.json").is_file()
    one = json.loads((root / "attempt-1" / "stand.json")
                     .read_text(encoding="utf-8"))
    assert one["results"][0]["result"] == "passed"


def test_каталог_окружение_подпроцессов(iso, catalog_repo, monkeypatch,
                                        solver_stub):
    """§7 п. 33. Окружение подпроцессов: у стенда, цепочки, сборки и
    pytest — вычистка префиксов гейтов, у CLI агента её нет (иначе агент
    остался бы без своих переменных), у гейтов временного каталога —
    свой каталог в `CREW_TEMPLATE_CATALOG`."""
    repo, ev, _ = catalog_repo
    rq = _request()
    row = _catalog_cr(repo, rq)
    calls = []

    def effect():
        (repo / "vmp" / "criteria.py").write_text("# 7\n", encoding="utf-8")
        _candidate_of(repo, rq, row["id"])
        return 0, "ок"
    monkeypatch.setattr(ev, "_run",
                        _catalog_run_mock(repo, ev, effect, calls=calls))
    crq.implement_cr(row["id"], ADMIN)
    scrub = ev._GATE_ENV_SCRUB
    for c in calls:
        joined = " ".join(c["cmd"])
        if c["cmd"][0] == ev.claude_cli():
            assert c["scrub"] == ()
        elif any(x in joined for x in ("vmp.stand_tasks", "vmp.bindings_",
                                       "pytest", "vmp.solver_build",
                                       "template_catalog.candidates",
                                       "parity.py")):
            assert c["scrub"] == tuple(scrub), joined
    gates = [c for c in calls if "--catalog" in c["cmd"]
             and "vmp.stand_tasks" in " ".join(c["cmd"])]
    assert gates and gates[0]["env"].get("CREW_TEMPLATE_CATALOG")
    # Закон паритета читает двери продуктов (L10) — им нужен ТОТ ЖЕ
    # временный каталог, что судится; переменную отдаёт зовущая сторона
    # (контракт §1 «Окружение»).
    law = [c for c in calls if "parity.py" in " ".join(c["cmd"])]
    assert len(law) == 1, [c["cmd"] for c in law]
    assert (law[0]["env"].get("CREW_TEMPLATE_CATALOG")
            == gates[0]["env"].get("CREW_TEMPLATE_CATALOG"))
    # и идёт он ПОСЛЕ генератора привязок попытки: судить нечем иначе
    order = [i for i, c in enumerate(calls)
             if "vmp.bindings_build" in " ".join(c["cmd"])
             and "--out" in c["cmd"]]
    assert order and calls.index(law[0]) > order[-1]


def test_каталог_формы_kotlin_видов_совпадают_с_каталогом():
    """§7 п. 34. Свидетель формы ожиданий (0.17): ключи `STAND_FORMS`
    трёх видов решателя равны ключам ожиданий тех же видов в ЖИВОМ
    каталоге. Литерал в коде без такого свидетеля молчалив."""
    from pathlib import Path

    from webapp import catalog_pipeline as cp
    root = Path(__file__).resolve().parents[1] / "catalog"
    seen: dict = {}
    for name, key in (("families.json", "families"),
                      ("criteria.json", "criteria")):
        doc = json.loads((root / name).read_text(encoding="utf-8"))
        for rec in doc.get(key) or ():
            for task in rec.get("stand_tasks") or ():
                for kind, expected in (task.get("expected") or {}).items():
                    if kind.startswith("kotlin_") and isinstance(expected,
                                                                 dict):
                        seen.setdefault(kind, set()).update(expected.keys())
    assert set(seen) == {"kotlin_constraint", "kotlin_logic_form",
                         "kotlin_metric"}, sorted(seen)
    for kind, keys in seen.items():
        assert set(cp.STAND_FORMS[kind]) == keys, kind


def test_recover_interrupted_зовёт_сборщик_сирот(iso, solver_stub):
    """§7 п. 36. Подметание после перезапуска снимает не только строки:
    эфемерный решатель прошлой попытки мог пережить приложение и держит
    архивы артефакта открытыми."""
    crq._add_crs([{"id": "cr0be5a1f2", "agent": "catalog-executor",
                   "kind": "catalog-executor", "title": "t",
                   "summary": "s", "status": "implementing",
                   "created_at": crq._now(), "updated_at": crq._now()}],
                 unique_by_id=True)
    crq.recover_interrupted()
    assert solver_stub["orphans"] == 1
    assert crq.get_cr("cr0be5a1f2")["status"] == "needs_human"


def test_каталог_задание_агенту_несёт_контракт_kotlin(iso, catalog_copy):
    """§7 п. 37. В задании агенту по виду решателя: имена открытых
    файлов, «ТОЛЬКО этот блок» у TaskModes, команда самопроверки,
    различающие задачи и близнецы, законность роста таблиц — и
    по-прежнему ни слов решения владельца, ни машинной формы правила."""
    from vmp import catalog_access as ca
    from webapp import catalog_pipeline as cp
    rq = _request(kinds=("kotlin_constraint", "kotlin_knob"))
    catalog_copy([rq])
    row = cp.build_row(rq, ca.current())
    prompt = crq._impl_prompt(row)
    for path in cp.KOTLIN_AGENT_FILES:
        assert path.rsplit("/", 1)[-1] in prompt, path
    assert "ТОЛЬКО этот блок" in prompt
    assert "python -m vmp.solver_build check --records duty_limits" in prompt
    assert "близнец" in prompt and "РАЗНЫМИ ожиданиями matches" in prompt
    assert "ЗАКОНЕН" in prompt
    assert "ОТЛОЖЕНО (не делай):\n- (нет)" in prompt
    # Закон паритета (§2.5 пакета «паритет на временном каталоге»): рост
    # таблиц решателя обязан быть ОБЪЯВЛЕН записью кандидата, и агент
    # видит тот же вердикт, что даст пятый гейт, — своей командой.
    assert row["spec"]["parity_law"] == cp.PARITY_LAW
    assert "ЗАКОН ПАРИТЕТА:" in prompt
    assert "объявила запись кандидата" in prompt.replace("ОБЪЯВИЛА",
                                                         "объявила")
    assert ("python catalog/tools/parity.py --with-candidates"
            in row["work"]["commands"])
    assert "python catalog/tools/parity.py --with-candidates" in prompt
    assert row["work"]["commands"].index(
        "python catalog/tools/parity.py --with-candidates") < \
        row["work"]["commands"].index("python -m pytest -q")
    # ни слов решения владельца, ни машинной формы правила
    assert "разборе" not in prompt and '"rule"' not in prompt


def test_каталог_задание_агенту_несёт_спецификацию_без_правил(
        iso, catalog_copy):
    from vmp import catalog_access as ca
    from webapp import catalog_pipeline as cp
    rq = _request()
    catalog_copy([rq])
    row = cp.build_row(rq, ca.current())
    prompt = crq._impl_prompt(row)
    assert "families/duty_limits" in prompt
    assert "check_code" in prompt
    assert "catalog/candidates/families/duty_limits.json" in prompt
    assert "NEEDS_HUMAN" in prompt
    # ни слов решения владельца, ни машинной формы правила
    assert "разборе" not in prompt and '"rule"' not in prompt
    assert "R-" not in prompt.replace("CR", "")


def test_каталог_задание_несёт_итоги_прошлой_попытки(iso, catalog_copy):
    """Вердикт судей прошлой попытки едет агенту в задание следующей
    (принцип контура 08.09.2026: судьи добавляются, агент не подменяется
    человеком). Живой случай 09.09.2026, CR duty_series_limit: попытка #3
    — стенд «судьи символа у стенда нет» и красный набор клона; без
    блока попытка #4 повторила бы то же. Едет только обезличенное:
    красные строки стенда с заметками, слова не-«ok» гейтов, красный
    паритет, имена красных тестов; первой попытке наследовать нечего."""
    from vmp import catalog_access as ca
    from webapp import catalog_pipeline as cp
    rq = _request()
    catalog_copy([rq])
    row = cp.build_row(rq, ca.current())
    assert crq._previous_attempt_block(row) == ""
    assert "ИТОГИ ПРОШЛОЙ ПОПЫТКИ" not in crq._impl_prompt(row)
    row["impl"] = {
        "gates_report": {
            "stand": {"rows_full": [
                {"record": "families/duty_limits", "executor_kind": "check_code",
                 "task": "st-1", "result": "failed",
                 "note": "судьи символа '_phantom' у стенда нет: вид check_code "
                         "судим"},
                {"record": "families/duty_limits", "executor_kind": "check_code",
                 "task": "st-2", "result": "passed", "note": ""}]},
            "paths": "ok",
            "bindings_gate": "B04: запись в зоне, но вид не объявлен",
            "parity": {"counts": {"red": 1}, "words": "L05 красный: имя без свидетеля"}},
        "tests_tail": "...\nFAILED tests/test_x.py::test_y - AssertionError: 1 != 2\n"
                      "FAILED tests/test_x.py::test_z - KeyError\n"
                      "FAILED tests/catalog_service/test_w.py::test_v - assert 2 == 1\n"
                      "3 failed, 5 passed\n"}
    block = crq._previous_attempt_block(row)
    assert block.startswith("ИТОГИ ПРОШЛОЙ ПОПЫТКИ")
    assert "_phantom" in block and "st-1" in block and "st-2" not in block
    assert "bindings_gate" in block and "B04" in block
    assert "- paths" not in block                       # «ok» не пересказывается
    assert "паритет: красных 1" in block and "L05" in block
    # красные тесты делятся по зоне агента: свои — устранить, чужие
    # (стенды службы каталога и т. п.) чинят судьи — агенту лишь не
    # красить их (попытка #4, 09.09.2026: 21 тест вне зоны агента)
    mine = next(ln for ln in block.splitlines() if "красные в вашей зоне" in ln)
    theirs = next(ln for ln in block.splitlines() if "ВНЕ вашей зоны" in ln)
    assert "tests/test_x.py::test_y" in mine and "tests/test_x.py::test_z" in mine
    assert "tests/catalog_service/test_w.py::test_v" in theirs
    assert "tests/catalog_service/test_w.py::test_v" not in mine
    assert "не красить" in theirs
    prompt = crq._impl_prompt(row)
    assert "ИТОГИ ПРОШЛОЙ ПОПЫТКИ" in prompt
    assert prompt.index("ИТОГИ ПРОШЛОЙ ПОПЫТКИ") < prompt.index("ЧТО СДАТЬ")
    # обезличенность блока — та же, что у всего задания
    assert "разборе" not in prompt and '"rule"' not in prompt


# ------------------------------------------- шов дорожек А и Б (§9.1)
#
# Тесты выше судят конвейер на ПОДСТАВНОМ `vmp.solver_build` (дорожка Б
# не ждала дорожку А). Этот — единственный, который берёт НАСТОЯЩИЙ
# модуль: подставной свидетель молчит, когда подпись в дереве разошлась
# с той, по которой писался конвейер.


def test_шов_конвейер_зовёт_настоящий_модуль_сборки(monkeypatch, tmp_path):
    """§9.1. Имена, подписи, константы и формы словарей настоящего
    `vmp/solver_build.py` — те, по которым написан конвейер.

    Судится ровно то, что конвейер употребляет: вызовы с ИМЕНОВАННЫМИ
    ключами (их проверяет `bind`), собственные бюджеты и слова классов
    против модульных, разбор командной строки сборки настоящим
    `argparse` модуля и ключи отчёта решателя, по которым гейт судит
    зелёное."""
    import inspect

    from vmp import solver_build as sb

    # 1. Модуль, который берёт конвейер, — тот самый, а не подделка.
    mod, words = crq._solver_module_or_words()
    assert mod is sb and words is None

    # 2. Вызовы конвейера связываются с настоящими подписями.
    inspect.signature(sb.probe).bind(
        tmp_path, java_home=None, mvn=None, venv_python="py")
    inspect.signature(sb.test_hygiene).bind(tmp_path, "main", ["литерал"])
    inspect.signature(sb.tables_growth).bind({}, {})
    inspect.signature(sb.scrub).bind("текст", roots=[str(tmp_path)])
    inspect.signature(sb.reap_orphans).bind()
    inspect.signature(sb.EphemeralSolver.__init__).bind(
        None, tmp_path, cr_key="cr1", log_path=tmp_path / "solver.log",
        java_home=None, xmx=crq.SOLVER_XMX_RUN,
        start_timeout_s=crq.SOLVER_START_TIMEOUT_S)
    assert callable(getattr(sb, "artifact_identity", None)), (
        "повторный отпечаток артефакта после стенда снимает модуль")

    # 3. Слова класса и бюджеты конвейера равны модульным: разойдутся —
    #    свидетельство назовёт класс, которого модуль не знает.
    assert (crq.SOLVER_CLASS_EXEC, crq.SOLVER_CLASS_TABLES) \
        == (sb.CLASS_ISP, sb.CLASS_TABL)
    бюджеты = {
        "SOLVER_PROBE_TIMEOUT_S": sb.PROBE_TIMEOUT_S,
        "SOLVER_BUILD_TIMEOUT_S": sb.BUILD_TIMEOUT_S,
        "SOLVER_TEST_TIMEOUT_S": sb.TEST_TIMEOUT_S,
        "SOLVER_START_TIMEOUT_S": sb.START_TIMEOUT_S,
        "SOLVER_STOP_TIMEOUT_S": sb.STOP_TIMEOUT_S,
    }
    for имя, предел in бюджеты.items():
        assert getattr(crq, имя) >= предел, имя
    assert (crq.SOLVER_XMX_MAVEN, crq.SOLVER_XMX_FORK, crq.SOLVER_XMX_RUN) \
        == (sb.XMX_MAVEN_DEFAULT, sb.XMX_FORK_DEFAULT, sb.XMX_SOLVER_DEFAULT)

    # 4. Рост таблиц: конвейер перебирает ключи словаря как есть — их
    #    ровно девять, и все они из перечня модуля.
    рост = sb.tables_growth({}, {})
    assert tuple(рост) == sb.TABLE_KEYS and not any(рост.values())

    # 5. Команда сборки разбирается настоящим `argparse` модуля.
    monkeypatch.setenv("COSMOS_SOLVER_JAVA_HOME", str(tmp_path / "jdk"))
    monkeypatch.setenv("COSMOS_SOLVER_MVN", str(tmp_path / "mvnw.cmd"))
    monkeypatch.setenv("COSMOS_SOLVER_M2", str(tmp_path / "m2"))
    argv: list = []

    class _EV:
        _GATE_ENV_SCRUB = ()

        def _run(self, cmd, **kw):
            argv[:] = cmd[3:]
            return 0, ""

    crq._run_solver_build(_EV(), tmp_path, "py", tmp_path / "attempt-1",
                          "0" * 40, with_tests=True)
    assert argv[0] == "build"
    разобрано: dict = {}
    monkeypatch.setattr(sb, "_cmd_build",
                        lambda a: разобрано.update(vars(a)) or sb.RC_OK)
    assert sb.main(argv) == sb.RC_OK
    assert разобрано["source"] == "commit"
    assert разобрано["commit"] == "0" * 40
    assert разобрано["with_tests"] is True
    assert (разобрано["xmx_maven"], разобрано["xmx_fork"]) \
        == (crq.SOLVER_XMX_MAVEN, crq.SOLVER_XMX_FORK)
    for ключ in ("attempt", "json", "java_home", "mvn", "m2"):
        assert разобрано.get(ключ), ключ

    # 5б. Подставной отчёт сборки, на котором стоят тесты выше, не
    #     богаче настоящего: лишний ключ подделки — это гейт, судящий
    #     то, чего модуль не пишет. Скелет берётся у самого модуля
    #     (сборка без коммита обрывается словами, ничего не запуская).
    скелет = sb.build(tmp_path, attempt=tmp_path / "skeleton",
                      source="commit", commit=None)
    assert скелет["available"] is False and скелет["words"]
    лишние = set(_build_json()) - set(скелет)
    assert not лишние, лишние
    # `artifact.dir` — единственная вольность подделки: настоящий модуль
    # путь не отдаёт (он проскрублен), и конвейер берёт его от папки
    # попытки. Свидетель этого — здесь, чтобы вольность не забылась.
    assert "dir" not in (скелет.get("artifact") or {})

    # 6. Отчёт НЕзапущенного решателя несёт все ключи, по которым гейт
    #    судит зелёное, и зелёным не притворяется.
    решатель = crq._solver_factory(tmp_path / "quarkus-app", cr_key="cr1",
                                   log_path=tmp_path / "solver.log")
    assert isinstance(решатель, sb.EphemeralSolver)
    отчёт = решатель.report()
    for ключ in ("url", "port", "pid", "pid_matches_port", "jvm", "banner",
                 "limits", "started_in_s", "attempts",
                 "artifact_unchanged_after", "stopped", "port_free_after",
                 "log_tail", "words"):
        assert ключ in отчёт, ключ
    assert отчёт["stopped"] not in ("terminated", "killed")
    assert отчёт["artifact_unchanged_after"] is None      # заполняет Б

    # 7. Пределы живого прогона конвейер и модуль берут из ОДНОГО места.
    пределы = crq._solver_limits()
    assert "words" not in пределы, пределы
    for ключ in ("spent_limit_s", "wait_ready_s", "poll_s",
                 "http_timeout_s"):
        assert пределы[ключ] == отчёт["limits"][ключ], ключ
    assert пределы["start_timeout_s"] == crq.SOLVER_START_TIMEOUT_S
    print(f"рассмотрено имён шва: {len(бюджеты) + len(рост) + 14}")


# --------------- 38–46. находки ревью пакета «Kotlin-исполнители» 08.09

def test_каталог_kotlin_судья_токенов_судит_и_тесты_агента(
        iso, catalog_repo, monkeypatch, solver_stub):
    """§7 п. 38. Код ТЕСТА агента — тоже измеряемое, а не судья.

    Тесты решателя исполняет surefire под учётной записью службы, в
    scratch-копии внутри клона и ДО стенда, pytest и гейтов: тест с
    записью файла переписал бы судью, и все трое сказали бы «зелено», а
    чистка ветки стёрла бы след. Судятся и написание с пробелом, и
    полное имя аннотации, и импорт."""
    repo, ev, _ = catalog_repo
    rq = _request(kinds=("kotlin_constraint",))
    row = _catalog_cr(repo, rq)
    monkeypatch.setattr(crq, "CR_IMPL_DAILY_LIMIT", 20)
    случаи = [
        ('class NewTest {\n  val ф = File ("../../../../vmp/stand_solver.py")'
         '\n}\n', "File("),
        ('class NewTest {\n  val p = Path.of("/x")\n}\n', "Path.of("),
        ("import java.nio.file.Files\nclass NewTest {}\n", "import"),
        ('class NewTest {\n  @jakarta.ws.rs.Path("/x") fun a() {}\n}\n',
         "@Path("),
    ]
    for текст, чем in случаи:
        crq._update_cr(row["id"], {"status": "accepted"})
        monkeypatch.setattr(ev, "_run", _catalog_run_mock(
            repo, ev, _kotlin_effect(
                repo, rq, row["id"], kinds=("kotlin_constraint",),
                test_text=текст + f'val c = "{_CONSTRAINT}"\n')))
        out = crq.implement_cr(row["id"], ADMIN)
        assert out["status"] == "needs_human", (чем, out["impl"])
        слова = out["impl"]["gates_report"]["paths"]
        assert "читает среду, диск или сеть" in слова, (чем, слова)
        assert _NEW_TEST in слова, (чем, слова)


def test_каталог_сторож_дерева_ловит_правку_судьи_сборкой(
        iso, catalog_repo, monkeypatch, solver_stub):
    """§7 п. 39. Дерево клона, изменённое ЗА ВРЕМЯ сборки и тестов
    решателя, обрывает суд словами: код агента (его тесты) исполняется
    до остальных судей, и подменённый судья сказал бы «зелено» трижды.
    Ни решателя, ни стенда после этого не бывает."""
    repo, ev, _ = catalog_repo
    rq = _request(kinds=("kotlin_constraint",))
    row = _catalog_cr(repo, rq)

    def подмена():
        (repo / "vmp" / "stand_solver.py").write_text(
            "# судья, переписанный тестом агента\n", encoding="utf-8")
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(
        repo, ev, _kotlin_effect(repo, rq, row["id"],
                                 kinds=("kotlin_constraint",)),
        build_side=подмена))
    out = crq.implement_cr(row["id"], ADMIN)
    assert out["status"] == "needs_human"
    слова = out["impl"]["gates_report"]["tree_guard"]
    assert "дерево клона изменено" in слова and "stand_solver.py" in слова
    assert solver_stub["solvers"] == [], "решатель после подмены не поднят"
    assert crq._gates_green(out["impl"]["gates_report"]) is False


def test_каталог_судья_диапазона_сверяет_текст_вне_блока_с_main(
        iso, catalog_repo, monkeypatch):
    """§7 п. 40. Судья диапазона `TaskModes.kt` сверяет текст ВНЕ блока
    с `main` побайтно: заметка `/* METRIC_EXECUTORS mapOf( */` границ не
    двигает, упоминание имени выше объявления — тоже, а чистое удаление
    строки вне блока (у него нет ни одной добавленной строки) больше не
    проходит молча."""
    repo, ev, _ = catalog_repo
    путь = repo / _TASK_MODES_PATH

    def судить(текст):
        путь.write_text(текст, encoding="utf-8")
        ev._git(repo, "add", "-A")
        слова = crq._kotlin_hunk_problems(ev, repo, {},
                                          [("M", _TASK_MODES_PATH)])
        ev._git(repo, "reset")
        return слова

    # 1. Правка ВНУТРИ блока — зелено (новая метрика).
    внутри = _TASK_MODES.replace(
        '    "credit_hours_spread" to MetricExecutor(',
        '    "layover_nights_total" to MetricExecutor(\n'
        '        constraint = "Layover nights away from home",\n'
        '        tasks = setOf("roster"),\n'
        '    ),\n'
        '    "credit_hours_spread" to MetricExecutor(')
    assert судить(внутри) == [], "правка внутри блока законна"
    # 2. Обёртка блока заметками: границы по тексту ВЕТКИ ушли бы на весь
    #    файл, и любая правка стала бы «внутри блока».
    обёртка = ("/* METRIC_EXECUTORS mapOf( */\n"
               + _TASK_MODES.replace(
                   "fun modeOf(name: String): String = name",
                   'fun modeOf(name: String): String = "x"')
               + "/* ) */\n")
    слова = судить(обёртка)
    assert слова and "правка вне METRIC_EXECUTORS" in слова[0], слова
    # 3. Чистое УДАЛЕНИЕ строки вне блока — красное (добавленных нет).
    удаление = _TASK_MODES.replace(
        "fun modeOf(name: String): String = name\n", "")
    слова = судить(удаление)
    assert слова and "правка вне METRIC_EXECUTORS" in слова[0], слова
    assert "ПОСЛЕ блока" in слова[0], слова
    # 4. Упоминание имени ВЫШЕ объявления не сдвигает начало блока.
    заметка = _TASK_MODES.replace(
        "data class MetricExecutor",
        "// см. METRIC_EXECUTORS ниже\ndata class Metric2(val a: Int)\n"
        "data class MetricExecutor")
    слова = судить(заметка)
    assert слова and "ДО блока" in слова[0], слова


def test_каталог_судья_токенов_при_настройке_git_без_начал(
        iso, catalog_repo, monkeypatch):
    """§7 п. 41. Настройка git `diff.noprefix=true` печатает заголовок
    диффа без начала `b/`. Судья токенов обязан судить и тогда: молчание
    «не нашёл ни одной строки» выдавало бы себя за зелёный гейт."""
    repo, ev, _ = catalog_repo
    subprocess.run(["git", "-C", str(repo), "config", "diff.noprefix",
                    "true"], check=True, capture_output=True)
    путь = repo / _PROVIDER
    путь.write_text(путь.read_text(encoding="utf-8")
                    + 'val home = System.getenv ("HOME")\n',
                    encoding="utf-8")
    ev._git(repo, "add", "-A")
    слова = crq._kotlin_token_problems(ev, repo, {}, [("M", _PROVIDER)])
    ev._git(repo, "reset")
    assert слова and "читает среду, диск или сеть" in слова[0], слова


def test_каталог_белый_список_знает_прихожую_пакета_и_переименование(
        iso, catalog_repo, monkeypatch):
    """§7 п. 42. `vmp/__init__.py` и `vmp/run/__init__.py` — судьи: их
    исполняет каждый импорт `vmp.…`, в том числе в процессе снимка
    таблиц. Переименование судьи в разрешённую папку судится по ОБОИМ
    путям: без этого удаление теста-судьи проходило бы белый список."""
    from webapp import catalog_pipeline as cp
    repo, _ev, _ = catalog_repo
    rq = _request(kinds=("kotlin_constraint",))
    row = _catalog_cr(repo, rq)
    for путь in ("vmp/__init__.py", "vmp/run/__init__.py"):
        assert путь in cp.FORBIDDEN_PATHS
        assert crq._catalog_path_problems(row, [("M", путь)]), путь
    # Переименование: исходный путь судится как удаление.
    пары = crq._name_status("R100\ttests/test_judge.py\ttests/vmp/helper.py")
    assert ("D", "tests/test_judge.py") in пары
    плохо = crq._catalog_path_problems(row, пары)
    assert плохо and "tests/test_judge.py" in плохо[0], плохо


def test_каталог_близнецы_среди_задач_записи_и_по_предмету(
        iso, catalog_repo, monkeypatch, solver_stub):
    """§7 п. 43. Близнецы ищутся среди ВСЕХ задач записи (кандидат —
    запись целиком, и копия существующей задачи образует пару со своим
    оригиналом), а пара по ЧУЖОМУ ограничению ручку не судит: иначе
    новое ограничение, ручку не читающее, «исполняло» бы её парой по
    давно зелёному соседу."""
    from vmp import catalog_access as ca
    repo, ev, _ = catalog_repo
    rq = _request(kinds=("kotlin_knob",))
    row = _catalog_cr(repo, rq)
    свои = list(ca.current().family("duty_limits")["stand_tasks"])
    twin_a = _copy_task("st-duty-2", "st-twin-a", matches=1, amount=1,
                        params={"max_duty_duration": 780,
                                "max_landings_per_duty": 1})
    twin_b = _copy_task("st-duty-2", "st-twin-b", matches=0, amount=0,
                        params={"max_duty_duration": 780,
                                "max_landings_per_duty": 9})
    binds = [{"kind": "kotlin_knob", "state": "есть",
              "file": f"{_SOLVER_DIR}/RunThresholds.kt",
              "symbol": "RunThresholds.KNOBS", "cr_key": None}]
    rows = _kotlin_rows([("st-twin-a", 1, "passed"),
                         ("st-twin-b", 0, "passed")])

    def run(tasks, seed=None):
        return _catalog_run_mock(
            repo, ev, _kotlin_effect(
                repo, rq, row["id"], tasks=tasks, kinds=("kotlin_knob",),
                seed=seed or [_kotlin_seed("kotlin_knob")]),
            rows=rows, bindings=binds)
    # Существующие задачи записи СОХРАНЕНЫ — годная пара найдена.
    monkeypatch.setattr(ev, "_run", run(свои + [twin_a, twin_b]))
    out = crq.implement_cr(row["id"], ADMIN)
    assert out["status"] == "implemented", out["impl"]["gates_report"]
    twins = out["impl"]["gates_report"]["solver"]["stand"]["twins"]
    assert twins[0]["tasks"] == ["st-twin-a", "st-twin-b"], twins
    # Пара названа одним ограничением — тем, чью ручку она судит (оба
    # близнеца сделаны копией задачи st-duty-2 живого каталога).
    assert twins[0]["constraint"] == "Max landings per duty", twins
    # Пара по ЧУЖОМУ ограничению при посеве нового ограничения записи —
    # красное словами о предмете.
    crq._update_cr(row["id"], {"status": "accepted"})
    чужие = [_copy_task("st-duty-2", "st-alien-a", constraint="Чужое",
                        matches=1, amount=1,
                        params={"max_duty_duration": 780,
                                "max_landings_per_duty": 1}),
             _copy_task("st-duty-2", "st-alien-b", constraint="Чужое",
                        matches=0, amount=0,
                        params={"max_duty_duration": 780,
                                "max_landings_per_duty": 9})]
    monkeypatch.setattr(ev, "_run", run(
        чужие, seed=[_kotlin_seed("kotlin_knob"), _kotlin_seed()]))
    out2 = crq.implement_cr(row["id"], ADMIN)
    assert out2["status"] == "needs_human"
    слова = out2["impl"]["gates_report"]["solver"]["stand"]["words"]
    assert "по предмету" in слова and _CONSTRAINT in слова, слова


def test_каталог_гейт_требует_состоявшихся_сверок_решателя():
    """§7 п. 44. Решение 0.16 требует для `implemented` СОСТОЯВШИХСЯ
    сверок: «не проверено» (`None`) — не «сошлись», а записанное
    изменение артефакта за время стенда — прямой сигнал подмены."""
    import copy
    зелёный = {
        "paths": "ok", "executor_files": "ok", "tree_guard": "ok",
        "parity_witness": "ok",
        "chain_live": "ok", "pytest": "green", "candidate": "ok",
        "bindings_build": "ok", "bindings_gate": "ok",
        "catalog_validate_errors": 0, "kotlin_tests": "ok",
        "candidate_reload": "ok", "parity": {"rc": 0, "ok": True},
        "stand": {"failed": 0},
        "solver": {"class": "К-исп", "parity": None, "tables_words": None,
                   "build": {"available": True},
                   "stand": {"words": None},
                   "run": {"url": "http://127.0.0.1:1",
                           "stopped": "terminated",
                           "port_free_after": True,
                           "pid_matches_port": True,
                           "artifact_unchanged_after": None}}}
    assert crq._gates_green(зелёный) is True

    def с(поле, значение):
        doc = copy.deepcopy(зелёный)
        doc["solver"]["run"][поле] = значение
        return doc
    assert crq._gates_green(с("pid_matches_port", None)) is False
    assert crq._gates_green(с("pid_matches_port", False)) is False
    assert crq._gates_green(с("port_free_after", None)) is False
    assert crq._gates_green(с("artifact_unchanged_after", False)) is False
    assert crq._gates_green(с("artifact_unchanged_after", True)) is True
    сорный = dict(зелёный, tree_guard="дерево клона изменено")
    assert crq._gates_green(сорный) is False
    # Пятый гейт (пакет «паритет на временном каталоге»): закон обязан
    # СКАЗАТЬ «зелено». Ни отсутствие вердикта, ни «не прогонялся», ни
    # красный не пускают строку в `implemented`; класс правки К-табл при
    # зелёном законе, наоборот, ей больше не мешает.
    без = dict(зелёный)
    без.pop("parity")
    assert crq._gates_green(без) is False
    assert crq._gates_green(dict(зелёный, parity={
        "rc": None, "ok": False, "words": "не прогонялся"})) is False
    assert crq._gates_green(dict(зелёный, parity={
        "rc": 1, "ok": False, "words": "L05: …"})) is False
    табл = copy.deepcopy(зелёный)
    табл["solver"]["class"] = "К-табл"
    табл["solver"]["parity"] = ("рост таблиц решателя (knobs: x) — судит "
                                "закон паритета; вердикт см. gates.parity")
    assert crq._gates_green(табл) is True


def test_каталог_папка_попытки_по_максимуму_номеров(iso, catalog_repo):
    """§7 п. 45. Номер попытки — по максимуму существующих, а не по их
    количеству: удалённая владельцем ранняя папка сдвигала счёт назад, и
    следующая попытка садилась в чужую — журналы дописывались к прошлым,
    а отчёты прошлой попытки читались за свидетельство этой."""
    import shutil
    repo, _ev, _ = catalog_repo
    корень = repo / "_repro" / "catalog-cr-x1"
    первый, n1 = crq._attempt_dir(repo, "x1")
    второй, n2 = crq._attempt_dir(repo, "x1")
    assert (n1, n2) == (1, 2)
    (второй / "stand.json").write_text("{}", encoding="utf-8")
    shutil.rmtree(первый)                       # чистка диска владельцем
    третий, n3 = crq._attempt_dir(repo, "x1")
    assert n3 == 3 and третий != второй, (n3, третий.name)
    assert not any(третий.iterdir()), "папка попытки своя и пустая"
    assert (корень / "attempt-2" / "stand.json").is_file()


def test_каталог_сборка_из_продукта_и_красная_не_гасит_тулчейн(tmp_path):
    """§7 п. 46. Судья сборки исполняется ИЗ ПРОДУКТА (`cwd`), а судимое
    дерево называется ключом `--repo`: при `cwd=<клон>` первым в пути
    поиска модулей стоит клон, и `import vmp` исполнял бы `__init__.py`
    ветки агента внутри процесса судьи. Красные тесты гасят `ok`, но не
    `available`: доступность тулчейна — не исход суда."""
    from pathlib import Path
    журнал: list = []
    отчёт = {"available": True, "ok": False, "rc_test": 1,
             "artifact": {"app_jar_sha256": "a" * 64},
             "words": "сборка красная: тесты решателя вернули код 1"}

    class _EV:
        _GATE_ENV_SCRUB = ()

        def _run(self, cmd, cwd=None, timeout_s=None, **kw):
            журнал.append({"cmd": [str(c) for c in cmd], "cwd": str(cwd)})
            out = Path(cmd[cmd.index("--json") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(отчёт, ensure_ascii=False),
                           encoding="utf-8")
            return 1, "тесты красные"

    клон = tmp_path / "clone"
    (клон / "_repro").mkdir(parents=True)
    doc = crq._run_solver_build(_EV(), клон, "py",
                               клон / "_repro" / "attempt-1", "0" * 40,
                               with_tests=True)
    cmd = журнал[0]["cmd"]
    assert cmd[cmd.index("--repo") + 1] == str(клон)
    assert журнал[0]["cwd"] == str(crq._product_root())
    assert Path(журнал[0]["cwd"]) != клон
    assert doc["available"] is True, "тулчейн был — красны тесты"
    assert doc["ok"] is False and doc["rc_cli"] == 1
    слова = crq._build_problems(doc, with_tests=True, kinds=[],
                                hygiene={"skipped_main": 0})
    assert слова == ["тесты решателя: код 1"], слова


def test_каталог_решатель_берёт_java_порядком_и_target_удаляется(
        iso, catalog_repo, monkeypatch, solver_stub):
    """§7 п. 47. Дом Java эфемерного решателя — по ТОМУ ЖЕ порядку
    поиска, что у пробы и сборки (0.12): одна ручка проекта оставляла
    решателю `java` из PATH, и на площадке с `JAVA_HOME` или с JDK
    установщика в `<клон>/.tools` проба и сборка были зелёными, а старт
    падал — после потраченной квоты. Папка `target/` попытки удаляется
    при любом исходе (просьба И-2 о свободном месте)."""
    repo, ev, _ = catalog_repo
    rq = _request()
    row = _catalog_cr(repo, rq)
    дом = repo / ".tools" / "jdk21"
    solver_stub_mod = __import__("vmp").solver_build
    solver_stub_mod.java_home_order = lambda repo_, java_home=None: (
        дом, "<клон>/.tools")

    def effect():
        (repo / "vmp" / "criteria.py").write_text("# java\n",
                                                  encoding="utf-8")
        _candidate_of(repo, rq, row["id"])
        return 0, "ок"
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(repo, ev, effect))
    out = crq.implement_cr(row["id"], ADMIN)
    assert out["status"] == "implemented", out["impl"]["gates_report"]
    assert solver_stub["solvers"][0].java_home == дом
    assert solver_stub["solvers"][0].tag == "конт7"
    target = (repo / "_repro" / f"catalog-cr-{row['id']}" / "attempt-1"
              / "kotlin-solver" / "target")
    assert not target.exists(), "target/ попытки удалён"


def test_каталог_target_удаляется_и_при_исключении(
        iso, catalog_repo, monkeypatch, solver_stub):
    """§7 п. 48. Исключение шагов 7–9 проходит не только через остановку
    решателя, но и через удаление `target/`: прежде сотни мегабайт
    сборки оставались в папке попытки при каждом красном прогоне."""
    repo, ev, _ = catalog_repo
    rq = _request(kinds=("kotlin_constraint",))
    row = _catalog_cr(repo, rq)
    base = _catalog_run_mock(repo, ev, _kotlin_effect(
        repo, rq, row["id"], kinds=("kotlin_constraint",)))

    def boom(cmd, cwd, timeout_s, **kw):
        if "template_catalog.candidates" in " ".join(str(c) for c in cmd):
            raise RuntimeError("проверка кандидата упала")
        return base(cmd, cwd, timeout_s, **kw)
    monkeypatch.setattr(ev, "_run", boom)
    with pytest.raises(crq.ChangeRequestError):
        crq.implement_cr(row["id"], ADMIN)
    assert solver_stub["solvers"][0].stopped is True
    target = (repo / "_repro" / f"catalog-cr-{row['id']}" / "attempt-1"
              / "kotlin-solver" / "target")
    assert not target.exists(), "target/ удалён и на исключительном пути"


def test_каталог_судьи_под_tests_vmp_агентом_не_правятся(iso, catalog_repo):
    """Просьба В-1 пакета паритета: под `tests/` — включая `tests/vmp/` —
    агенту дозволены только НОВЫЕ файлы.

    В `tests/vmp/` живут судьи, на которых стоит `implemented`: гейт
    п. 13 «ничего не потеряно» и опись `LOST_WITH_REASON`
    (`tests/vmp/test_bindings.py`), проекции снимка заморозки
    (`test_template_registry_wrapper.py`). Пока правка этих файлов
    проходила белый список, условие `pytest == "green"` в клоне ничего
    не доказывало: судью правил тот, кого он судит."""
    repo, _ev, _ = catalog_repo
    row = _catalog_cr(repo, _request())
    for путь in ("tests/vmp/test_bindings.py",
                 "tests/vmp/test_template_registry_wrapper.py",
                 "tests/vmp/test_stand_tasks.py"):
        for статус in ("M", "D"):
            плохо = crq._catalog_path_problems(row, [(статус, путь)])
            assert плохо and "изменён существующий тест" in плохо[0], \
                (статус, путь, плохо)
    # Новый файл под тем же началом — законная зона агента.
    assert crq._catalog_path_problems(
        row, [("A", "tests/vmp/test_cr_probe.py")]) == []


def test_каталог_правка_свидетеля_паритета_красная(
        iso, catalog_repo, monkeypatch, solver_stub):
    """Просьба В-2: перечни `vmp/run/roster_certify.py`, по которым закон
    паритета судит природу записи (L15), её условия (L16) и
    сертификатные шаблоны проверок (L09), снимаются ДО и ПОСЛЕ работы
    агента. Их правка — `needs_human` словами: иначе «рост объявлен
    записью» самосертифицируем — агент меняет свидетеля, и запись любой
    природы становится «по коду». Файл целиком закрыть нельзя: он
    исполнитель строк «есть» посева."""
    repo, ev, _ = catalog_repo
    rq = _request()
    row = _catalog_cr(repo, rq)

    def effect():
        (repo / "vmp" / "criteria.py").write_text("# исполнитель\n",
                                                  encoding="utf-8")
        _candidate_of(repo, rq, row["id"])
        return 0, "дописал исполнителя"

    правленый = dict(_CERTIFY_MAIN)
    правленый["WORLD_PARAM_FAMILIES"] = sorted(
        list(_CERTIFY_MAIN["WORLD_PARAM_FAMILIES"]) + ["duty_limits"])
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(
        repo, ev, effect, certify=правленый))
    out = crq.implement_cr(row["id"], ADMIN)
    отчёт = out["impl"]["gates_report"]
    assert out["status"] == "needs_human", отчёт
    слова = отчёт["parity_witness"]
    assert "правка свидетеля паритета" in слова, слова
    assert "WORLD_PARAM_FAMILIES" in слова, слова
    assert crq._gates_green(отчёт) is False


def test_каталог_агент_зовётся_без_ворот_разрешений(iso, catalog_repo, monkeypatch):
    """Агент CR каталога обязан идти с выключенными воротами разрешений CLI
    (как агент внедрения): умолчание рабочего места «план» иначе оставляет
    его без права правок, и вместо работы приходит план (сквозная
    проверка 08.09.2026)."""
    repo, ev, _ = catalog_repo
    rq = _request()
    row = _catalog_cr(repo, rq)
    seen = []

    def effect():
        return 0, "готово"

    real = _catalog_run_mock(repo, ev, effect)

    def spy(cmd, cwd, timeout_s, env_extra=None, input_text=None,
            on_line=None, scrub_prefixes=()):
        cmd = [str(c) for c in cmd]
        if cmd[0] == ev.claude_cli():
            seen.append(cmd)
        return real(cmd, cwd, timeout_s, env_extra=env_extra,
                    input_text=input_text, on_line=on_line,
                    scrub_prefixes=scrub_prefixes)
    monkeypatch.setattr(ev, "_run", spy)
    out = crq.implement_cr(row["id"], ADMIN)
    assert seen, "агент не вызывался"
    assert "--dangerously-skip-permissions" in seen[0]
    assert "--disallowedTools" in seen[0]
    # Модель агента названа заранее (слово владельца 09.09.2026: coding =
    # claude-opus-5 умолчанием кода) и записана в свидетельство строки.
    model = crq.settings.cli_model("coding")
    assert model and seen[0][seen[0].index("--model") + 1] == model
    assert out["impl"]["agent_model"] == model


def test_каталог_таймаут_агента_становится_needs_human_словами(iso, catalog_repo, monkeypatch):
    """Агент, снятый по таймауту (код 124) или упавший с иным кодом, даёт
    строке честный исход needs_human со словами, а не голую ошибку ответа
    (сквозная проверка 08.09.2026: строка возвращалась в accepted без следа)."""
    repo, ev, _ = catalog_repo
    rq = _request()
    row = _catalog_cr(repo, rq)

    def effect():
        return 124, "TIMEOUT after 7200.0s: claude -p"
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(repo, ev, effect))
    out = crq.implement_cr(row["id"], ADMIN)
    assert out["status"] == "needs_human"
    assert out["impl"]["needs_human"] is True
    assert "124" in out["impl"]["words"] and "таймаут" in out["impl"]["words"]


def test_каталог_фабрика_решателя_принимает_метку_контура():
    """Фабрика эфемерного решателя обязана принимать `tag` (метка контура в
    командной строке JVM) и везти его в EphemeralSolver: сквозная проверка
    09.09.2026 — после зелёной сборки решатель не поднимался словами
    «got an unexpected keyword argument 'tag'»."""
    import inspect
    sig = inspect.signature(crq._solver_factory)
    assert "tag" in sig.parameters
    from vmp import solver_build as sb
    assert "tag" in inspect.signature(sb.EphemeralSolver.__init__).parameters
    assert crq.IMPL_TEST_TIMEOUT_S >= 2400


def test_суточная_квота_реализаций_из_переменной_рабочего_места(iso, monkeypatch):
    """Квота — CR_IMPL_DAILY_LIMIT (3) либо CREW_CR_IMPL_DAILY_LIMIT
    рабочего места владельца (09.09.2026: три сквозные проверки утром
    исчерпали умолчание до заявки владельца); мусор и пусто — умолчание,
    отрицательное — ноль (реализация выключена)."""
    monkeypatch.delenv("CREW_CR_IMPL_DAILY_LIMIT", raising=False)
    assert crq._impl_daily_limit() == crq.CR_IMPL_DAILY_LIMIT == 3
    assert crq._impl_quota_left() == 3
    monkeypatch.setenv("CREW_CR_IMPL_DAILY_LIMIT", "10")
    assert crq._impl_daily_limit() == 10 and crq._impl_quota_left() == 10
    for bad in ("", "  ", "много"):
        monkeypatch.setenv("CREW_CR_IMPL_DAILY_LIMIT", bad)
        assert crq._impl_daily_limit() == 3
    monkeypatch.setenv("CREW_CR_IMPL_DAILY_LIMIT", "-2")
    assert crq._impl_daily_limit() == 0 and crq._impl_quota_left() == 0
    with pytest.raises(crq.ChangeRequestError):
        crq._impl_quota_take()
    monkeypatch.setenv("CREW_CR_IMPL_DAILY_LIMIT", "2")
    crq._impl_quota_take()
    crq._impl_quota_take()
    assert crq._impl_quota_left() == 0
    with pytest.raises(crq.ChangeRequestError, match="2/day"):
        crq._impl_quota_take()


# ----------------------------------- предполётная проверка задания (шаг 0-П)
#
# 09.09.2026 первая новая семья каталога дошла до записи с ПЯТОЙ попытки
# агента: четыре сорвались не о шаблон, а о ДОПУЩЕНИЯ СУДЕЙ — судья был
# написан на неподвижный каталог. Предполётная проверка ищет их ДО
# агента: по строке CR строится заготовка записи (виды исполнителей
# объявлены, самих исполнителей нет) и по ней прогоняются те же дешёвые
# судьи. Ожидаемые пробелы едут агенту перечнем «что обязано закрыться»;
# допущение судьи — «нужен человек» БЕЗ запуска агента.

def _живая_заявка_образец() -> dict:
    """Настоящая заявка дерева — ОБРАЗЕЦ формы, а не подопытная.

    Берётся первая (по номеру) заявка раздела семей с черновиком —
    такая, что доехала до записи каталога (посадка категории C, состояние
    «есть»). Тест берёт её ФОРМУ и строит по ней синтетическую заявку:
    судить живую очередь дерева тестом нельзя — она меняется выпусками,
    а живой номер заявки в тесте — литерал среды (сторож
    tests/test_no_environment_literals.py)."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    doc = json.loads((root / "catalog" / "requests.json")
                     .read_text(encoding="utf-8-sig"))
    rows = sorted((rq for rq in doc.get("requests") or []
                   if isinstance(rq, dict) and rq.get("section") == "families"
                   and rq.get("draft")),
                  key=lambda rq: str(rq.get("request_id")))
    return rows[0] if rows else {}


def _строка_cr_с_заготовкой() -> dict:
    """Строка CR по СИНТЕТИЧЕСКОЙ заявке формы живой: черновик новой
    семьи, один объявленный вид исполнителя, исполнителя ещё нет."""
    from template_catalog import growth
    образец = _живая_заявка_образец()
    assert образец.get("section") == "families" and образец.get("draft"), \
        "образец формы заявки в дереве не найден"
    draft = growth.draft_of(growth.probe_family(
        mode_name=growth.MODE_EXTENSION))
    draft["id"] = "zz_probe_preflight"
    rid = growth.probe_request_id()
    return {
        "id": growth.cr_key_of(rid),
        "kind": crq.CATALOG_KIND,
        "title": "Исполнитель записи families/zz_probe_preflight",
        "record": {"section": "families", "record_id": "zz_probe_preflight",
                   "request_id": rid, "request_form": "новая запись"},
        "spec": {"draft": draft, "record_now": None,
                 "executors_requested": [{"kind": "check_code", "hint": ""}]},
        "work": {},
    }


def test_предполётная_проверка_называет_ожидаемые_пробелы_заготовки(tmp_path):
    """На ЖИВОМ каталоге дерева с заготовкой настоящей формы вердикт —
    `ok` либо `expected-gaps`: судьи называют только то, что обязан
    закрыть агент («исполнителя нет», «стендовых задач нет»), и каждая
    находка НАЗЫВАЕТ запись по имени."""
    import sys
    from pathlib import Path

    from template_catalog import candidates as tcc
    from webapp import evolution_runner as ev
    root = Path(__file__).resolve().parents[1]
    cr = _строка_cr_с_заготовкой()
    rep = crq.preflight_catalog_cr(cr, repo=root, py=sys.executable, ev=ev,
                                   tmp=tmp_path / "pf", with_tests=False)
    assert rep["verdict"] in (crq.PREFLIGHT_OK, crq.PREFLIGHT_GAPS), rep
    assert all(f["kind"] == "gap" for f in rep["findings"]), rep["findings"]
    assert all("zz_probe_preflight" in f["words"] for f in rep["findings"])
    assert any("исполнителя нет" in f["words"] for f in rep["findings"])
    assert any("исполнителя нет" in g and "check_code" in g
               for g in rep["gaps"])
    # заготовка — файл-кандидат той же формы, что напишет агент
    cand = json.loads(Path(rep["candidate"]).read_text(encoding="utf-8"))
    assert tcc.load_candidate(rep["candidate"])["id"] == "zz_probe_preflight"
    assert cand["record"]["status"] == "есть"
    assert cand["record"]["stand_tasks"] == []
    assert "history" not in cand["record"]
    # ожидаемые пробелы едут в задание агенту перечнем «что обязано
    # закрыться»
    блок = crq._preflight_block({**cr, "preflight": rep})
    assert "ЧТО ОБЯЗАНО ЗАКРЫТЬСЯ" in блок and "исполнителя нет" in блок


def test_предполётная_проверка_ловит_судью_зашитого_на_число_записей(
        tmp_path, monkeypatch):
    """Судья, зашитый на ЧИСЛО записей каталога, жалуется, не назвав
    записи, — это допущение «каталог не растёт», а не пробел заготовки.
    Вердикт `judge-assumption`."""
    import sys
    from pathlib import Path

    import template_catalog as tc
    from webapp import evolution_runner as ev
    root = Path(__file__).resolve().parents[1]

    def судья_на_число(ctx):
        было = len(tc.load(root / "catalog").families())
        стало = len(tc.load(ctx["folder"]).families())
        if стало != было:
            return [f"в каталоге ожидалось {было} семей, найдено {стало}"]
        return []
    monkeypatch.setitem(crq.PREFLIGHT_JUDGES, "число_записей", судья_на_число)
    rep = crq.preflight_catalog_cr(_строка_cr_с_заготовкой(), repo=root,
                                   py=sys.executable, ev=ev,
                                   tmp=tmp_path / "pf", with_tests=False)
    assert rep["verdict"] == crq.PREFLIGHT_ASSUMPTION, rep["findings"]
    плохие = [f for f in rep["findings"] if f["kind"] == "assumption"]
    assert [f["judge"] for f in плохие] == ["число_записей"], плохие
    assert "семей" in плохие[0]["words"]
    assert "ДОПУЩЕНИЕ судей" in rep["words"]


def test_допущение_судьи_останавливает_реализацию_до_агента(
        iso, catalog_repo, monkeypatch):
    """Найденное допущение — «нужен человек» ДО агента: облачная модель
    не зовётся, квота реализации дня не тратится, а находки лежат в
    строке CR (это дешевле пяти попыток)."""
    repo, ev, _ = catalog_repo
    rq = _request()
    row = _catalog_cr(repo, rq)
    зовы: list = []

    def судья_на_число(ctx):
        return ["в каталоге ожидалось 17 записей, найдено 18"]
    monkeypatch.setitem(crq.PREFLIGHT_JUDGES, "число_записей", судья_на_число)

    def effect():
        зовы.append("агент")
        return 0, "готово"
    monkeypatch.setattr(ev, "_run", _catalog_run_mock(repo, ev, effect))
    было = crq._impl_quota_left()
    out = crq.implement_cr(row["id"], ADMIN)
    assert out["status"] == "needs_human"
    assert зовы == [], "агент не должен зваться после находки допущения"
    assert crq._impl_quota_left() == было, "квота дня не тратится"
    pf = out["preflight"]
    assert pf["verdict"] == crq.PREFLIGHT_ASSUMPTION
    assert any(f["judge"] == "число_записей" for f in pf["findings"])
    assert "ДОПУЩЕНИЕ судей" in out["impl"]["report"]


def test_ожидаемые_пробелы_пускают_реализацию_и_едут_в_задание(
        iso, catalog_repo, monkeypatch):
    """Ожидаемые пробелы заготовки реализацию НЕ останавливают: агент
    запускается, а перечень пробелов вложен в его задание."""
    repo, ev, _ = catalog_repo
    rq = _request()
    row = _catalog_cr(repo, rq)
    задания: list = []

    def effect():
        (repo / "vmp" / "criteria.py").write_text("# исполнитель 9\n",
                                                  encoding="utf-8")
        _candidate_of(repo, rq, row["id"])
        return 0, "дописал исполнителя"
    базовый = _catalog_run_mock(repo, ev, effect)

    def запись(cmd, cwd, timeout_s, **kw):
        if kw.get("input_text"):
            задания.append(kw["input_text"])
        return базовый(cmd, cwd, timeout_s, **kw)
    monkeypatch.setattr(ev, "_run", запись)
    out = crq.implement_cr(row["id"], ADMIN)
    pf = out["preflight"]
    assert pf["verdict"] in (crq.PREFLIGHT_OK, crq.PREFLIGHT_GAPS), pf
    assert задания, "агент обязан быть зван"
    if pf["verdict"] == crq.PREFLIGHT_GAPS:
        assert "ЧТО ОБЯЗАНО ЗАКРЫТЬСЯ" in задания[0]
