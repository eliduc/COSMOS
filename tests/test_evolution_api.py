"""Admin API for the Evolution lab: GET /api/evolution returns every
improvement finding to admins and 403s plain catalog users. Dev-mode
client fixture as in test_users_admin.py: the user DB and admin audit
log are redirected to a tmp dir — the real webapp/config is never
touched.
"""
import importlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from _stand_identity import ADMIN_EMAIL

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN_EMAIL)
    from webapp import users, admin_api, evolution_runner
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(admin_api, "AUDIT_LOG_PATH", tmp_path / "audit.log")
    # Эти тесты про эндпоинты, а не про изоляцию — у неё отдельные
    # тесты. Заглушка явная: честная isolation_problems() теперь никогда
    # не пуста (запуск под отдельной записью в коде не реализован).
    monkeypatch.setattr(evolution_runner, "isolation_problems", lambda: [])
    users.ensure_db()
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c, users


def test_admin_lists_evolution_findings(client):
    c, _users = client
    from webapp import evolution
    created, row = evolution.add_finding(
        "Дуальная стабилизация прайсинга",
        "Стабилизация двойственных цен ускоряет генерацию колонок.",
        source_url="https://example.org/papers/dual-stabilization")
    assert created

    resp = c.get("/api/evolution")
    assert resp.status_code == 200
    improvements = resp.json()["improvements"]
    assert any(i["id"] == row["id"] and
               i["title"] == "Дуальная стабилизация прайсинга"
               for i in improvements)


def test_plain_user_forbidden_on_evolution(client):
    c, users = client
    users.add("plain@example.com", "user", by=ADMIN_EMAIL)
    os.environ["CREW_DEV_USER"] = "plain@example.com"
    try:
        assert c.get("/api/evolution").status_code == 403
    finally:
        os.environ["CREW_DEV_USER"] = ADMIN_EMAIL


def test_scan_trigger_disabled_conflicts(client):
    """«Начать R&D» is a 409 no-op when evolution is off (default in tests)."""
    c, _users = client
    resp = c.post("/api/evolution/scan")
    assert resp.status_code == 409


def test_scan_trigger_admin_starts(client, monkeypatch):
    c, _users = client
    from webapp import evolution_runner
    monkeypatch.setattr(evolution_runner, "enabled", lambda: True)
    started = {"n": 0}
    monkeypatch.setattr(evolution_runner, "trigger_scan",
                        lambda: (started.__setitem__("n", started["n"] + 1)
                                 or True))
    resp = c.post("/api/evolution/scan")
    assert resp.status_code == 200 and resp.json()["started"] is True
    assert started["n"] == 1
    # a second press while a scan is running -> 409
    monkeypatch.setattr(evolution_runner, "trigger_scan", lambda: False)
    assert c.post("/api/evolution/scan").status_code == 409


def test_scan_trigger_plain_user_forbidden(client):
    c, users = client
    users.add("plain@example.com", "user", by=ADMIN_EMAIL)
    os.environ["CREW_DEV_USER"] = "plain@example.com"
    try:
        assert c.post("/api/evolution/scan").status_code == 403
    finally:
        os.environ["CREW_DEV_USER"] = ADMIN_EMAIL


def test_evolution_action_endpoint(client, monkeypatch):
    """The in-app pipeline buttons: launch Test for a fresh finding, gate
    duplicate presses (409), reject, 404 for unknown ids, 400 for unknown
    actions."""
    import threading as _t
    c, _users = client
    from webapp import evolution, evolution_runner
    _created, row = evolution.add_finding("Кнопка Test", "проверка кнопок")
    started = _t.Event()
    monkeypatch.setattr(evolution_runner, "run_test_phase",
                        lambda imp_id, **kw: started.set())
    resp = c.post(f"/api/evolution/{row['id']}/action",
                  json={"action": "test"})
    assert resp.status_code == 200
    assert resp.json()["status"] == evolution.STATUS_APPROVED_FOR_TEST
    assert started.wait(5)
    # A second press: no longer a valid predecessor -> 409.
    assert c.post(f"/api/evolution/{row['id']}/action",
                  json={"action": "test"}).status_code == 409
    # Reject is valid from any non-terminal status.
    resp = c.post(f"/api/evolution/{row['id']}/action",
                  json={"action": "reject"})
    assert resp.status_code == 200
    assert resp.json()["status"] == evolution.STATUS_REJECTED
    assert c.post("/api/evolution/999999/action",
                  json={"action": "test"}).status_code == 404
    assert c.post(f"/api/evolution/{row['id']}/action",
                  json={"action": "fly"}).status_code == 400


def test_evolution_action_plain_user_forbidden(client):
    c, users = client
    from webapp import evolution
    _created, row = evolution.add_finding("Чужая кнопка", "нет прав")
    users.add("plain@example.com", "user", by=ADMIN_EMAIL)
    os.environ["CREW_DEV_USER"] = "plain@example.com"
    try:
        assert c.post(f"/api/evolution/{row['id']}/action",
                      json={"action": "test"}).status_code == 403
    finally:
        os.environ["CREW_DEV_USER"] = ADMIN_EMAIL


def test_scan_activity_feed(client):
    c, _users = client
    from webapp import evolution_runner
    evolution_runner._activity_reset()
    evolution_runner._activity_add("search", "crew pairing 2026")
    resp = c.get("/api/evolution/scan-activity?since=0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is True
    assert any(e["kind"] == "search" for e in body["events"])
    last = body["last_seq"]
    # since=last -> only newer events (none)
    assert c.get(f"/api/evolution/scan-activity?since={last}").json()[
        "events"] == []
    evolution_runner._activity_finish()


def test_scan_activity_plain_user_forbidden(client):
    c, users = client
    users.add("plain@example.com", "user", by=ADMIN_EMAIL)
    os.environ["CREW_DEV_USER"] = "plain@example.com"
    try:
        assert c.get("/api/evolution/scan-activity").status_code == 403
    finally:
        os.environ["CREW_DEV_USER"] = ADMIN_EMAIL


def test_unisolated_environment_asks_the_admin_instead_of_refusing(
        client, monkeypatch):
    """Кнопка «Реализовать» на неукреплённой машине не отказывает, а
    называет препятствия и ждёт подтверждения. Глухой отказ выключал
    контур самоулучшения целиком (правка 2026-07-26)."""
    import threading as _t
    c, _users = client
    from webapp import evolution, evolution_runner
    # фикстура client глушит isolation_problems -> []; здесь нужна как
    # раз НЕизолированная среда
    monkeypatch.setattr(evolution_runner, "isolation_problems",
                        lambda: ["агент работает под той же учётной записью, что и приложение (COSMOS_AGENT_ACCOUNT)"])
    monkeypatch.delenv(evolution_runner._UNISOLATED_OK, raising=False)
    started = _t.Event()
    seen = {}
    monkeypatch.setattr(
        evolution_runner, "run_test_phase",
        lambda imp_id, **kw: (seen.update(kw), started.set()))
    _created, row = evolution.add_finding("Изоляция", "проверка диалога")

    # первое нажатие: 409 с перечнем препятствий, статус не меняется
    resp = c.post(f"/api/evolution/{row['id']}/action",
                  json={"action": "test"})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "isolation_unconfirmed"
    assert detail["problems"] and any("COSMOS_AGENT_ACCOUNT" in p
                                      for p in detail["problems"])
    assert evolution.get(row["id"])["status"] == row["status"]
    assert not started.is_set()

    # подтверждение: фаза запускается, признак доезжает до неё
    resp2 = c.post(f"/api/evolution/{row['id']}/action",
                   json={"action": "test", "confirm_unisolated": True})
    assert resp2.status_code == 200
    assert resp2.json()["unisolated_confirmed"] is True
    assert started.wait(5)
    assert seen == {"acknowledged_unisolated": True}


# ---------------- оценка эффективности находок (вкладка «Алгоритмы») ----

def test_impact_scoring_endpoints_start_and_poll(client, monkeypatch):
    """POST /evolution/score стартует батч (один за раз), GET
    /evolution/score-status отдаёт снимок. LLM не вызывается —
    подменяется весь раннер (сеть в тестах запрещена).

    Контур явно объявляется оценочным: тестовое дерево не совпадает с
    COSMOS_TEST_DIR, а гейт контура проверяется отдельными тестами ниже."""
    c, _users = client
    from webapp import solver_advisor
    monkeypatch.setattr(solver_advisor, "scoring_allowed", lambda: (True, ""))
    calls = []

    def fake_start(backend="cloud"):
        calls.append(backend)
        return {"running": True, "already_running": False,
                "started_at": "t", "finished_at": None, "total": 0,
                "scored": 0, "skipped": 0, "failed": 0, "error": None}

    monkeypatch.setattr(solver_advisor, "start_impact_scoring", fake_start)
    resp = c.post("/api/evolution/score", json={})
    assert resp.status_code == 200
    assert calls == ["cloud"]

    monkeypatch.setattr(
        solver_advisor, "start_impact_scoring",
        lambda backend="cloud": {"already_running": True, "running": True})
    assert c.post("/api/evolution/score", json={}).status_code == 409

    monkeypatch.setattr(
        solver_advisor, "impact_status",
        lambda: {"running": False, "scored": 2, "skipped": 1,
                 "failed": 0, "total": 3, "error": None,
                 "started_at": "t", "finished_at": "t2"})
    st = c.get("/api/evolution/score-status")
    assert st.status_code == 200
    assert st.json()["scored"] == 2


def test_impact_scoring_unknown_backend_rejected(client):
    c, _users = client
    assert c.post("/api/evolution/score",
                  json={"backend": "banana"}).status_code == 400


# --- гейт контура оценки: балл считает ровно одна установка (внешняя) ---

def _pretend_contour(monkeypatch, *, is_scoring: bool, tmp_path):
    """Подменяет корень установки и COSMOS_TEST_DIR так, чтобы контур
    оказался оценочным или внутренним."""
    from webapp import evolution_runner, solver_advisor
    here = tmp_path / "cosmos-test"
    other = tmp_path / "crew-demo"
    here.mkdir(exist_ok=True)
    other.mkdir(exist_ok=True)
    monkeypatch.setattr(solver_advisor, "REPO_ROOT",
                        here if is_scoring else other)
    monkeypatch.setattr(evolution_runner, "test_dir", lambda: here)


def test_scoring_allowed_only_on_external_contour(tmp_path, monkeypatch):
    from webapp import solver_advisor
    _pretend_contour(monkeypatch, is_scoring=True, tmp_path=tmp_path)
    ok, reason = solver_advisor.scoring_allowed()
    assert ok and reason == ""

    _pretend_contour(monkeypatch, is_scoring=False, tmp_path=tmp_path)
    ok, reason = solver_advisor.scoring_allowed()
    assert not ok and "Test" in reason


def test_scoring_refused_when_contour_undetermined(monkeypatch):
    """Неизвестность = запрет: испортить чужие баллы хуже, чем не считать."""
    from webapp import evolution_runner, solver_advisor

    def boom():
        raise RuntimeError("COSMOS_TEST_DIR не разрешается")

    monkeypatch.setattr(evolution_runner, "test_dir", boom)
    ok, reason = solver_advisor.scoring_allowed()
    assert not ok and "контур не определён" in reason


def test_start_impact_scoring_refuses_and_writes_nothing(tmp_path,
                                                         monkeypatch):
    """Главный негативный: на внутреннем контуре батч НЕ стартует и не
    трогает уже синхронизированную оценку. Батч пишет attach_impact
    безусловно, поэтому один запуск затёр бы все баллы отказом модели."""
    from webapp import evolution, solver_advisor, users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    users.ensure_db()
    _created, row = evolution.add_finding(
        "Двухэтапная мастер-формулировка", "Смены, затем пейринги.",
        source_url="https://example.org/vance97")
    good = {"score": 55, "confidence": "med", "rationale": "по делу",
            "cache_key": "k1", "source": "llm", "model": "claude-sonnet-5"}
    evolution.attach_impact(row["id"], good)

    _pretend_contour(monkeypatch, is_scoring=False, tmp_path=tmp_path)
    called = []
    monkeypatch.setattr(solver_advisor, "score_finding",
                        lambda *a, **k: called.append(1))

    res = solver_advisor.start_impact_scoring("cloud")
    assert res.get("refused") is True
    assert called == [], "модель не должна вызываться на внутреннем контуре"
    assert evolution.get(row["id"])["impact"] == good


def test_evolution_list_reports_scoring_flag(client, monkeypatch):
    """Интерфейс узнаёт о запрете из ответа списка — кнопка прячется."""
    c, _users = client
    from webapp import solver_advisor
    monkeypatch.setattr(solver_advisor, "scoring_allowed",
                        lambda: (False, "оценка только на Test"))
    body = c.get("/api/evolution").json()
    assert body["scoring_allowed"] is False
    assert body["scoring_refused_reason"] == "оценка только на Test"
    assert c.post("/api/evolution/score", json={}).status_code == 409

    monkeypatch.setattr(solver_advisor, "scoring_allowed", lambda: (True, ""))
    assert c.get("/api/evolution").json()["scoring_allowed"] is True


def test_evolution_list_carries_impact_cache(client):
    """attach_impact сохраняет оценку, список отдаёт её обратно;
    статус находки не меняется (совещательная величина)."""
    c, _users = client
    from webapp import evolution
    _created, row = evolution.add_finding(
        "Ко-генерация колонн", "Совместная генерация колонн этапов.",
        source_url="https://example.org/p2")
    before = row["status"]
    evolution.attach_impact(row["id"], {
        "score": 73, "confidence": "med", "rationale": "прайсинг",
        "cache_key": "abc", "source": "llm"})
    resp = c.get("/api/evolution")
    imp = next(i for i in resp.json()["improvements"]
               if i["id"] == row["id"])
    assert imp["impact"]["score"] == 73
    assert imp["status"] == before


def test_evolution_action_forwards_countersign(client, monkeypatch):
    """Веб-действие продвижения несёт токен контрподписи серверу; отказ
    сервера возвращается структурированно (аудит R10, P0-04)."""
    c, _users = client
    from webapp import evolution_runner as er
    seen = {}

    def fake_approve(imp_id, action, email, acknowledged_unisolated=False,
                     countersign=None):
        seen.update(action=action, countersign=countersign)
        return {"ok": True, "status": "approved_for_stage"}

    monkeypatch.setattr(er, "approve_action", fake_approve)
    r = c.post("/api/evolution/5/action",
               json={"action": "stage", "countersign": "tok123"})
    assert r.status_code == 200
    assert seen == {"action": "s", "countersign": "tok123"}

    monkeypatch.setattr(
        er, "approve_action",
        lambda *a, **k: {"ok": False, "error": "countersign_required",
                         "message": "нужен токен"})
    r = c.post("/api/evolution/5/action", json={"action": "stage"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "countersign_required"


def test_evolution_list_carries_published_at(client):
    """set_published сохраняет дату публикации ПЕРВОИСТОЧНИКА; список
    отдаёт её (сортировка «по дате публикации» на вкладке «Алгоритмы»)."""
    c, _users = client
    from webapp import evolution
    _created, row = evolution.add_finding(
        "Стабилизация якорями", "Якорные точки в прайсинге.",
        source_url="https://arxiv.org/abs/2505.01234")
    assert row["published_at"] is None
    evolution.set_published(row["id"], "2025-05")
    resp = c.get("/api/evolution")
    imp = next(i for i in resp.json()["improvements"]
               if i["id"] == row["id"])
    assert imp["published_at"] == "2025-05"
    with pytest.raises(ValueError):
        evolution.set_published(row["id"], "")


def test_impact_batch_fills_published_even_on_cache_skip(client,
                                                         monkeypatch):
    """Батч оценки пишет published_at: из arXiv-URL — даже когда оценка
    взята из кэша (колонка даты появилась позже первых оценок), из
    ответа модели — когда URL даты не кодирует."""
    _c, _users = client
    from webapp import evolution
    from webapp import solver_advisor as sa
    _x, arx = evolution.add_finding(
        "ArXiv-находка", "С датой в идентификаторе.",
        source_url="https://arxiv.org/abs/2311.00001")
    _y, web = evolution.add_finding(
        "Веб-находка", "Дату знает только модель.",
        source_url="https://example.org/blog/colgen")
    skill_version = (sa.load_skill().get("meta") or {}).get("version")
    # arXiv-строка уже «оценена» с совпадающим ключом кэша -> skip-ветка
    evolution.attach_impact(arx["id"], {
        "score": 50, "confidence": "med", "rationale": "r",
        "source": "llm",
        "cache_key": sa.impact_cache_key(arx, skill_version)})

    def fake_score(finding, **kw):
        return {"score": 61, "confidence": "med", "rationale": "r",
                "published": "2024-07", "source": "llm",
                "cache_key": "k", "model": "m"}

    monkeypatch.setattr(sa, "score_finding", fake_score)
    sa._run_impact_batch("cloud")
    rows = {r["id"]: r for r in evolution.list_all()}
    assert rows[arx["id"]]["published_at"] == "2023-11"   # из URL, на skip
    assert rows[web["id"]]["published_at"] == "2024-07"   # из ответа модели
    assert sa.impact_status()["skipped"] >= 1
