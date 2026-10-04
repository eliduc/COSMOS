# -*- coding: utf-8 -*-
"""Механики восстановления цикла (живой случай BA 31.08): красный
гейт остановил канонизацию fail-closed; после правки кода СУДЬИ
дожать канон было нечем — повторный запуск породил цикл по
УСТАРЕВШЕМУ канону, и регресс-токены первого честно закричали об
«откате». Две ручки: снятие незавершённого цикла в архив (void) и
пересинхронизация канона от завершённого цикла (resync)."""
import json

import pytest
from fastapi.testclient import TestClient

from cdpm import exchange as ex, extract, freeze, model as m
from cdpm import webui
from test_cdpm_extract import _RULES_A, _RULES_B, fake_llm


@pytest.fixture()
def run_dir(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "RULES-A.md").write_text(_RULES_A, encoding="utf-8")
    (src / "RULES-B.md").write_text(_RULES_B, encoding="utf-8")
    freeze.freeze([src / "RULES-A.md", src / "RULES-B.md"],
                  tmp_path / "freeze")
    extract.run_extract(tmp_path / "freeze", tmp_path / "extract",
                        fake_llm, "Полярис (тест)")
    monkeypatch.setattr(webui, "_run_dir", lambda rid: tmp_path)
    (tmp_path / "status.json").write_text(
        json.dumps({"state": "извлечено"}, ensure_ascii=False),
        encoding="utf-8")
    return tmp_path


def _finish_cycle(run_dir):
    """Завершённый чистый цикл: аудитор без замечаний → вердикт."""
    def auditor(prompt):
        return json.dumps({"remarks": []}, ensure_ascii=False)

    def worker(prompt):
        return json.dumps({"answers": []}, ensure_ascii=False)

    return ex.run_exchange(run_dir / "freeze", run_dir / "extract",
                           run_dir / "exchange", auditor, worker,
                           max_rounds=1)


def test_void_снимает_только_незавершённый(run_dir):
    client = TestClient(webui.app)
    # Живого цикла нет — честный отказ.
    r = client.post("/api/runs/x/exchange/void")
    assert r.status_code == 409
    # Незавершённый (раунды без вердикта) — снимается в архив.
    rounds = run_dir / "exchange" / "rounds" / "round-01"
    rounds.mkdir(parents=True)
    (rounds / "remarks.json").write_text("{}", encoding="utf-8")
    r = client.post("/api/runs/x/exchange/void")
    assert r.status_code == 200
    assert r.json()["voided"] == "exchange-void-001"
    assert not (run_dir / "exchange").exists()
    assert (run_dir / "exchange-void-001" / "rounds"
            / "round-01" / "remarks.json").is_file()
    # Завершённый — не снимается.
    _finish_cycle(run_dir)
    r = client.post("/api/runs/x/exchange/void")
    assert r.status_code == 409
    assert "ЗАВЕРШЁН" in r.json()["detail"]


def test_resync_дожимает_канон_из_архива(run_dir):
    client = TestClient(webui.app)
    summary = _finish_cycle(run_dir)
    assert summary["verdict"] in ("чисто", "лимит")
    # Цикл уезжает в архив (как при живом случае BA), канон портим.
    (run_dir / "exchange").rename(run_dir / "exchange-rev-001")
    canon = run_dir / "extract" / "model.json"
    doc = m.load_model(canon)
    stamp = len(doc.get("rules", []))
    # Незавершённого нет — resync из архива обязан пройти.
    r = client.post("/api/runs/x/exchange/resync"
                    "?src=exchange-rev-001")
    assert r.status_code == 200, r.text[:300]
    got = r.json()
    assert got["src"] == "exchange-rev-001"
    assert got["canon_rules"] == stamp
    # Реестр и токены вернулись в свежий exchange.
    assert (run_dir / "exchange" / "registry.json").is_file()
    st = json.loads((run_dir / "status.json")
                    .read_text(encoding="utf-8"))
    assert st["state"].startswith("аудит-цикл: ")
    assert st["exchange_problems"] == []
    # Повторный resync при непустом exchange — честный отказ.
    r = client.post("/api/runs/x/exchange/resync"
                    "?src=exchange-rev-001")
    assert r.status_code == 409
    assert "void" in r.json()["detail"]


def test_resync_отказывает_кривому_источнику(run_dir):
    client = TestClient(webui.app)
    # Незавершённый цикл — пересинхронизировать нечего.
    rounds = run_dir / "exchange" / "rounds" / "round-01"
    rounds.mkdir(parents=True)
    r = client.post("/api/runs/x/exchange/resync")
    assert r.status_code == 409
    assert "не завершён" in r.json()["detail"]
    # Имя источника вне закона — 400.
    r = client.post("/api/runs/x/exchange/resync?src=..%2Fchuzhoe")
    assert r.status_code == 400


def test_операторские_миграции_вида_всё_или_ничего(run_dir):
    """Живой прогон нано (31.08): аудитор клаузам верные строки
    дефектом вида не считает — операторская ручка подаёт kind_patch
    напрямую под теми же гейтами формы; след — «операторским
    решением»; всё-или-ничего."""
    client = TestClient(webui.app)
    doc = m.load_model(run_dir / "extract" / "model.json")
    hard = next(r for r in doc["rules"] if r.get("kind") == "HARD")
    # Отказ пакета целиком при одной кривой миграции.
    r = client.post("/api/runs/x/kind-migrations", json=[
        {"rule_id": hard["id"], "kind": "DATA"},
        {"rule_id": "R-НЕТ", "kind": "DATA"}])
    assert r.status_code == 409
    assert "всё-или-ничего" in r.json()["detail"]
    doc2 = m.load_model(run_dir / "extract" / "model.json")
    got = next(x for x in doc2["rules"] if x["id"] == hard["id"])
    assert got["kind"] == "HARD", "канон не тронут"
    # Чистый пакет применяется, след операторский, канон переиздан.
    r = client.post("/api/runs/x/kind-migrations", json=[
        {"rule_id": hard["id"], "kind": "DATA"}])
    assert r.status_code == 200, r.text[:300]
    assert r.json()["applied"] == [hard["id"]]
    doc3 = m.load_model(run_dir / "extract" / "model.json")
    got = next(x for x in doc3["rules"] if x["id"] == hard["id"])
    # Детерминизация ярлыков (2026-09-02): «справка → DATA» у строки
    # из документа правил оседает видом DEF (данные — только из файлов
    # данных); след операторский и след кода — оба в примечании.
    assert got["kind"] == "DEF"
    assert "операторским решением" in got.get("note", "")
    assert "DATA→DEF" in got.get("note", "")


def test_вопрос_пересмотра_принятого_ответа(run_dir):
    """Право а/к передумать (живой прогон нано 31.08): ручка ставит
    ОТКРЫТЫЙ вопрос-пересмотр применённого ответа (наследование
    части/привязок, прежний ответ в тексте); открытый вопрос
    пересматривать нельзя (отвечается прямо)."""
    client = TestClient(webui.app)
    doc = m.load_model(run_dir / "extract" / "model.json")
    rule0 = doc["rules"][0]
    applied = {
        "id": "Q-3a-тест-применённый", "part": "3a",
        "title": "Порог", "text": "Назовите порог.",
        "answer_format": {"type": "value", "note": "часы"},
        "rule_refs": [rule0["id"]],
        "source_refs": [dict(s) for s in rule0["sources"][:1]] * 2,
        "answer": {"raw": "10 часов", "status": "применён",
                   "revision": 1}}
    doc["questions"].append(applied)
    m.dump_model(doc, run_dir / "extract" / "model.json")
    r = client.post(f"/api/runs/x/questions/{applied['id']}"
                    "/reconsider")
    assert r.status_code == 200, r.text[:300]
    new_id = r.json()["created"]
    assert new_id == applied["id"] + "-R"
    doc2 = m.load_model(run_dir / "extract" / "model.json")
    nq = next(x for x in doc2["questions"] if x["id"] == new_id)
    assert nq["part"] == applied["part"]
    assert "пересматривает" in nq["text"]
    assert nq.get("answer") is None
    # Открытый вопрос пересмотру не подлежит.
    r = client.post(f"/api/runs/x/questions/{new_id}/reconsider")
    assert r.status_code == 409
    # Несуществующий — 404.
    r = client.post("/api/runs/x/questions/Q-НЕТ/reconsider")
    assert r.status_code == 404
