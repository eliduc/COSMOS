# -*- coding: utf-8 -*-
"""Признак допуска моделей у КОМПЛЕКТА документов (решение владельца
2026-09-03, поправка «если признака нет — только локальные модели»).

Стенд с известным ответом ДО правки (принцип владельца), шесть случаев:
(а) синтетический комплект без строки признака → any по умолчанию,
    облачная роль проходит;
(б) проприетарный комплект → local-only, облако отказывает ScopeViolation;
(в) проприетарный с согласием авиакомпании в признаке → any, основание
    видно в чтении;
(г) комплект без класса данных и без признака → local-only, заявление
    «признак не выставлен, действует умолчание local-only»;
(д) просьба scope запроса, расходящаяся с признаком → отказ словами;
(е) площадка с прежней переменной CDPM_SCOPE_FORCE → переменная не
    действует, работа по признаку, след в чтении.
Плюс форма признака: проприетарный класс с допуском any без основания —
отказ; ручка kit-scope правит признак; извлечение пишет в статус
ЭФФЕКТИВНЫЙ контур."""
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from cdpm import webui
from webapp import llm_backend


def _run(tmp_path, monkeypatch, kit=None, run_id="run-0001"):
    root = tmp_path / "runs"
    monkeypatch.setenv("CDPM_RUNS_DIR", str(root))
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    status = {"state": "заморожено", "files": ["a.docx"]}
    if kit is not None:
        status["kit"] = kit
    webui._write_status(run_dir, status)
    return run_dir


def _seen_scope(monkeypatch):
    seen = {}
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: seen.setdefault("scope", scope))
    monkeypatch.setattr(webui.providers_mod, "load_agents", lambda run_dir: {})
    return seen


def test_а_синтетический_без_признака_облако_проходит(tmp_path, monkeypatch):
    monkeypatch.delenv("CDPM_SCOPE_FORCE", raising=False)
    run_dir = _run(tmp_path, monkeypatch, kit={"data_class": "synthetic"})
    ks = webui.kit_scope(webui._read_status(run_dir))
    assert ks["effective"] == "any" and ks["source"] == "умолчание по классу данных"
    seen = _seen_scope(monkeypatch)
    # S06-R2: облачный класс без объявленной раскладки не строится —
    # отказ словами (раскладку тест не объявляет); допуск при этом
    # доходит до шва канала локальным классом.
    with pytest.raises(HTTPException) as ei:
        webui._channel_for(None, run_dir, "worker", None, klass="cloud")
    assert ei.value.status_code == 409
    assert "раскладка моделей ролей не объявлена" in ei.value.detail
    webui._channel_for(None, run_dir, "worker", None, klass="local")
    assert seen["scope"] == "any"
    assert llm_backend.resolve_backend("any", "cloud") == "cloud"


def test_б_проприетарный_local_only_облако_отказывает(tmp_path, monkeypatch):
    monkeypatch.delenv("CDPM_SCOPE_FORCE", raising=False)
    run_dir = _run(tmp_path, monkeypatch, kit={"data_class": "proprietary"})
    ks = webui.kit_scope(webui._read_status(run_dir))
    assert ks["effective"] == "local-only"
    seen = _seen_scope(monkeypatch)
    webui._channel_for(None, run_dir, "worker", None, klass="cloud")
    assert seen["scope"] == "local-only"
    with pytest.raises(llm_backend.ScopeViolation):
        llm_backend.resolve_backend(seen["scope"], "cloud")
    assert llm_backend.resolve_backend(seen["scope"], "local") == "local"


def test_в_проприетарный_с_согласием_any_основание_видно(tmp_path, monkeypatch):
    monkeypatch.delenv("CDPM_SCOPE_FORCE", raising=False)
    run_dir = _run(tmp_path, monkeypatch, kit={
        "data_class": "proprietary", "llm_scope": "any",
        "llm_scope_basis": "письмо авиакомпании от 01.09.2026, п. 3"})
    ks = webui.kit_scope(webui._read_status(run_dir))
    assert ks["effective"] == "any" and ks["source"] == "признак комплекта"
    assert "письмо авиакомпании от 01.09.2026" in ks["reading"]
    assert webui._run_scope(run_dir, None) == "any"


def test_г_без_класса_и_признака_local_only_с_заявлением(tmp_path, monkeypatch):
    monkeypatch.delenv("CDPM_SCOPE_FORCE", raising=False)
    run_dir = _run(tmp_path, monkeypatch, kit=None)
    ks = webui.kit_scope(webui._read_status(run_dir))
    assert ks["effective"] == "local-only"
    assert ks["reading"] == "признак не выставлен, действует умолчание local-only"
    seen = _seen_scope(monkeypatch)
    webui._channel_for(None, run_dir, "worker", None, klass="cloud")
    assert seen["scope"] == "local-only"


def test_д_просьба_запроса_расходится_с_признаком_отказ_словами(tmp_path, monkeypatch):
    monkeypatch.delenv("CDPM_SCOPE_FORCE", raising=False)
    run_dir = _run(tmp_path, monkeypatch, kit={"data_class": "proprietary"})
    with pytest.raises(HTTPException) as ei:
        webui._run_scope(run_dir, "any")
    assert ei.value.status_code == 400
    assert "расходится с признаком комплекта" in ei.value.detail
    # Совпадающая просьба и её отсутствие — не отказ.
    assert webui._run_scope(run_dir, "local-only") == "local-only"
    assert webui._run_scope(run_dir, None) == "local-only"


def test_е_переменная_площадки_не_действует(tmp_path, monkeypatch):
    monkeypatch.setenv("CDPM_SCOPE_FORCE", "local-only")
    run_dir = _run(tmp_path, monkeypatch, kit={"data_class": "synthetic"})
    ks = webui.kit_scope(webui._read_status(run_dir))
    assert ks["effective"] == "any" and ks["env_force_ignored"] is True
    seen = _seen_scope(monkeypatch)
    # Локальным классом: облачный без раскладки — отказ S06-R2, а
    # судится здесь допуск, не раскладка.
    webui._channel_for(None, run_dir, "worker", None, klass="local")
    assert seen["scope"] == "any"


def test_форма_признака_проприетарный_any_без_основания_отказ():
    probs = webui.validate_kit_scope("proprietary", "any", "")
    assert probs and "основания" in probs[0]
    assert webui.validate_kit_scope("proprietary", "any", "письмо от 01.09") == []
    assert webui.validate_kit_scope("synthetic", None, None) == []
    assert webui.validate_kit_scope("secret", None, None)
    assert webui.validate_kit_scope(None, "cloud", None)


def test_ручка_kit_scope_и_статус(tmp_path, monkeypatch):
    monkeypatch.delenv("CDPM_SCOPE_FORCE", raising=False)
    run_dir = _run(tmp_path, monkeypatch, kit=None)
    with TestClient(webui.app) as c:
        st = c.get("/api/runs/run-0001").json()
        assert st["llm_scope"]["effective"] == "local-only"
        r = c.put("/api/runs/run-0001/kit-scope",
                  json={"data_class": "proprietary", "llm_scope": "any"})
        assert r.status_code == 400 and "основания" in r.json()["detail"]
        r = c.put("/api/runs/run-0001/kit-scope",
                  json={"data_class": "synthetic", "llm_scope": "any",
                        "llm_scope_basis": "набор синтетический"})
        assert r.status_code == 200 and r.json()["effective"] == "any"
        assert c.get("/api/runs/run-0001/kit-scope").json()["source"] == "признак комплекта"
        r = c.put("/api/runs/run-0001/kit-scope", json={"scope": "any"})
        assert r.status_code == 400
    kit = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))["kit"]
    assert kit == {"data_class": "synthetic", "llm_scope": "any",
                   "llm_scope_basis": "набор синтетический"}


def test_сброс_прогона_сохраняет_признак_комплекта(tmp_path, monkeypatch):
    """Признак — свойство комплекта, как исходные файлы: сброс
    результатов стадий его не снимает (иначе прогон после сброса
    молча шёл бы local-only)."""
    monkeypatch.delenv("CDPM_SCOPE_FORCE", raising=False)
    run_dir = _run(tmp_path, monkeypatch,
                   kit={"data_class": "synthetic", "llm_scope": None,
                        "llm_scope_basis": None})
    (run_dir / "freeze").mkdir()
    with TestClient(webui.app) as c:
        r = c.post("/api/runs/run-0001/reset")
        assert r.status_code == 200, r.text
        assert c.get("/api/runs/run-0001").json()["llm_scope"]["effective"] == "any"
