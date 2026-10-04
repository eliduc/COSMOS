"""Студия формализации (Ф1/Ф5): сессии, фиксация ответов с автором,
связки маппинга и Rule Pack, сборка «Пакета маппинга заказчика», API-гейт."""
import importlib

import pytest
from fastapi.testclient import TestClient

from webapp import formalization_studio as fs
from webapp import rule_packs as rp
from _stand_identity import ADMIN_EMAIL

ADMIN = ADMIN_EMAIL
USER = "someone@example.com"


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "SESSIONS_PATH", tmp_path / "studio.json")
    monkeypatch.setattr(rp, "PACKS_PATH", tmp_path / "packs.json")
    yield tmp_path


def test_session_lifecycle_and_answers(iso):
    s = fs.create_session({"name": "Заказчик А", "country": "ru"}, ADMIN)
    assert s["country"] == "RU" and s["version"] == 1
    fs.answer(s["id"], "inventory", "schedule_source",
              "Amadeus Altea, выгрузка SSIM еженедельно", ADMIN)
    fs.answer(s["id"], "glossary", "base_meaning",
              "База = аэропорт приписки; одна база", ADMIN)
    cur = fs.get_session(s["id"])
    rec = cur["inventory"]["answers"]["schedule_source"]
    assert rec["by"] == ADMIN and "Altea" in rec["answer"]
    assert cur["version"] == 3                       # два ответа = две правки
    # пустой текст удаляет ответ
    fs.answer(s["id"], "inventory", "schedule_source", "", ADMIN)
    assert "schedule_source" not in \
        fs.get_session(s["id"])["inventory"]["answers"]
    # незнакомый вопрос — громкий отказ
    with pytest.raises(fs.StudioError, match="unknown question"):
        fs.answer(s["id"], "glossary", "nope", "x", ADMIN)
    lst = fs.list_sessions()
    assert lst[0]["glossary_done"] == 1
    fs.delete_session(s["id"])
    assert fs.get_session(s["id"]) is None


def test_links_and_package(iso, monkeypatch):
    s = fs.create_session({"name": "Заказчик Б", "country": "xx"}, ADMIN)
    # rule pack link + answer
    pack = rp.create_pack({"name": "XQ", "country": "xx"}, ADMIN)
    fs.link_rule_pack(s["id"], pack["id"])
    with pytest.raises(fs.StudioError, match="no such rule pack"):
        fs.link_rule_pack(s["id"], "rpNOPE")
    fs.answer(s["id"], "glossary", "tz_limits", "Всё в UTC", ADMIN)
    # mapping link: несуществующий инстанс — отказ; план сохраняется
    with pytest.raises(fs.StudioError, match="no such instance"):
        fs.link_mapping(s["id"], {"instance_id": "nope"})
    fs.link_mapping(s["id"], {"plan": {"files": []},
                              "notes": "маппинг за 1 день"})
    pkg = fs.assemble_package(s["id"])
    assert pkg["package"] == "customer-mapping-package"
    assert pkg["country"] == "XX"
    tz = next(q for q in pkg["glossary"] if q["id"] == "tz_limits")
    assert tz["answer"] == "Всё в UTC" and tz["by"] == ADMIN
    unanswered = next(q for q in pkg["glossary"]
                      if q["id"] == "base_meaning")
    assert unanswered["answer"] is None              # честно: нет решения
    assert pkg["rule_pack"]["name"] == "XQ"
    assert pkg["mapping"]["plan"] == {"files": []}
    assert pkg["mapping"]["instance"] is None


# --------------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN)
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fs, "SESSIONS_PATH", tmp_path / "studio.json")
    monkeypatch.setattr(rp, "PACKS_PATH", tmp_path / "packs.json")
    users.ensure_db()
    users.add(USER, "user", by=ADMIN)
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def test_api_admin_only_and_flow(client, monkeypatch):
    r = client.post("/api/studio", json={"name": "X", "country": "ru"})
    assert r.status_code == 200
    sid = r.json()["id"]
    assert client.put(f"/api/studio/{sid}/answer",
                      json={"part": "inventory",
                            "question_id": "crew_source",
                            "text": "1С"}).status_code == 200
    pkg = client.get(f"/api/studio/{sid}/package")
    assert pkg.status_code == 200
    assert pkg.json()["package"] == "customer-mapping-package"
    monkeypatch.setenv("CREW_DEV_USER", USER)
    assert client.get("/api/studio").status_code == 403
    assert client.get(f"/api/studio/{sid}/package").status_code == 403
