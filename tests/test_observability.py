"""Точки наблюдения выпуска (P2-05, R5-09).

Пять раундов аудита подряд отмечали: `/health`, `/ready`, `/version` не
существовали, и запрос к ним попадал на общий обработчик статики —
наблюдатель получал 200 и страницу приложения ВСЕГДА, включая случай
сломанного контура. Здесь проверяется, что три точки отвечают по
существу, не требуют входа и не выдают наружу ничего лишнего.
"""
import importlib

import pytest
from fastapi.testclient import TestClient
from _stand_identity import ADMIN_EMAIL


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN_EMAIL)
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    users.ensure_db()
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def test_healthz_is_machine_readable_and_needs_no_login(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json"), \
        "наблюдателю нужен разбираемый ответ, а не страница приложения"
    assert r.json() == {"status": "ok"}


def test_readyz_reports_dependencies_and_503_when_not_ready(client,
                                                            monkeypatch):
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["ready"] is True
    assert set(r.json()["checks"]) >= {"payload_schema", "users_db",
                                       "rules_resolver"}
    # неготовность обязана быть КОДОМ, а не текстом внутри 200
    from webapp import build_identity
    monkeypatch.setattr(build_identity, "readiness",
                        lambda: {"ready": False,
                                 "checks": {"payload_schema": False}})
    bad = client.get("/readyz")
    assert bad.status_code == 503 and bad.json()["ready"] is False


def test_versionz_binds_runtime_to_source_and_solver(client):
    v = client.get("/versionz")
    assert v.status_code == 200
    body = v.json()
    assert body["schema"] == "cosmos-build-identity/v1"
    assert "commit" in body["source"] and "tree_dirty" in body["source"]
    solver = body["solver"]
    assert set(solver) >= {"jar_sha256", "manifest_jar_sha256",
                           "matches_manifest"}
    if solver["jar_present"]:
        # Пятый аудит (R5-08) нашёл фактическое расхождение развёрнутого
        # решателя с манифестом. Оно обязано быть ВИДНО снаружи.
        assert isinstance(solver["matches_manifest"], bool)


def test_observability_leaks_no_paths_or_settings(client):
    """Точки открыты без входа, поэтому не имеют права отдавать пути
    файловой системы, значения настроек и данные заказчика."""
    blob = "".join(client.get(p).text
                   for p in ("/healthz", "/readyz", "/versionz"))
    for needle in ("C:\\", "/home/", "sk-ant-", "ollama_url", "password",
                   "users.db-wal", "data/uploads"):
        assert needle not in blob, f"наружу утекает {needle!r}"
