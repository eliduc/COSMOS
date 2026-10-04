# -*- coding: utf-8 -*-
"""Слепой компилятор шестой проверки: разбор ответа читателя,
отбраковка видов вне словаря, контур local-only, честный отказ на
пустом ответе. Модель мокается — сеть в тестах не участвует."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from webapp import llm_backend, source_check_compile as scc  # noqa: E402

CLAUSES = [
    {"sid": "S1", "anchor": "табл.3, строка 2",
     "text": "Смена не длиннее 12:00."},
    {"sid": "S1", "anchor": "табл.3, строка 6",
     "text": "Штраф за ночёвку вне базы; величина не установлена."},
]


def _fake_complete(reply):
    def fn(prompt, *, backend, system=None, usage=None, model=None,
           timeout_s=0, effort=None, temperature=None,
           max_tokens=None, cwd=None):
        assert usage == "source_check"
        # Слепота: в запросе нет следов ВМП/конвейера формализации
        joined = (system or "") + prompt
        assert "ВМП" not in prompt
        assert "клауз" in joined.lower()
        return {"ok": True, "text": reply, "backend": backend,
                "model": "fake"}
    return fn


def _forbidden(name):
    """Заглушка примитива, которого на этом пути быть не должно."""
    def fn(*a, **kw):
        raise AssertionError(f"{name} звался, хотя не должен")
    return fn


ANSWER = json.dumps([
    {"id": "C1", "kind": "duty_span_max", "anchor": "табл.3, строка 2",
     "params": {"limit_min": 600, "rest_split_min": 480}}],
    ensure_ascii=False)


def _cli_recorder(reply, calls):
    """Заглушка llm_backend._cli_generate с сигнатурой примитива
    (prompt, timeout_s, cwd=None, model=None)."""
    def fn(prompt, timeout_s, cwd=None, model=None):
        calls.append((prompt, timeout_s, model))
        if isinstance(reply, Exception):
            raise reply
        return reply
    return fn


def test_разбор_и_отбраковка_видов(monkeypatch):
    reply = "Вот проверки:\n" + json.dumps([
        {"id": "a", "kind": "duty_span_max",
         "anchor": "табл.3, строка 2",
         "params": {"limit_min": 720, "rest_split_min": 480},
         "note": "12:00"},
        {"id": "b", "kind": "выдумка", "params": {}},
        {"id": "c", "kind": "not_machine_checkable",
         "anchor": "табл.3, строка 6",
         "params": {"reason": "мягкое: величина не установлена"}},
        "мусор",
    ], ensure_ascii=False)
    monkeypatch.setattr(llm_backend, "complete",
                        _fake_complete(reply))
    monkeypatch.setattr(llm_backend, "_cli_generate",
                        _forbidden("_cli_generate"))
    monkeypatch.setattr(llm_backend, "_api_generate",
                        _forbidden("_api_generate"))
    got = scc.compile_checks(CLAUSES, scope="any",
                             requested="cloud")
    kinds = [s["kind"] for s in got["specs"]]
    assert kinds == ["duty_span_max", "not_machine_checkable"]
    assert got["specs"][1]["reason"].startswith("мягкое")
    assert len(got["problems"]) == 2
    # прежний путь complete (канал не указан): канал не наблюдается,
    # прямые ветки канала не звались (S-03.2, S-05.6)
    assert got["channel"] is None
    assert scc.compile_checks(CLAUSES, scope="any", requested="cloud",
                              channel=None)["channel"] is None
    # скомпилированное исполняется настоящим исполнителем; смены режутся
    # показанием прогона о границе, не числом читателя rest_split_min
    # (GPT-059) — без показания duty_span_max честно «не проверяется»
    from vmp import source_check as sc
    empty = {"flights": [], "employees": [], "flightAssignments": []}
    res = sc.run_checks(got["specs"], empty,
                        rest_boundary={"rest_boundary_min": 480,
                                       "rest_boundary_basis": "knob",
                                       "rest_boundary_source": "стенд"})
    assert [r.verdict for r in res] == ["ok", "not_checkable"]
    res0 = sc.run_checks(got["specs"], empty)
    assert [r.verdict for r in res0] == ["not_checkable", "not_checkable"]
    assert "rest_split_min=480 не применяется" in res0[0].detail


@pytest.mark.parametrize("channel", [None, "cli", "api"])
def test_контур_local_only_не_пускает_облако(monkeypatch, channel):
    """Допуск судится ДО транспорта, включая прямые ветки канала
    (S-05.4): ни complete, ни _cli_generate, ни _api_generate."""
    monkeypatch.setattr(llm_backend, "complete", _forbidden("complete"))
    monkeypatch.setattr(llm_backend, "_cli_generate",
                        _forbidden("_cli_generate"))
    monkeypatch.setattr(llm_backend, "_api_generate",
                        _forbidden("_api_generate"))
    monkeypatch.setattr(llm_backend._settings, "has_api_key",
                        lambda: True)
    with pytest.raises(llm_backend.ScopeViolation):
        scc.compile_checks(CLAUSES, scope="local-only",
                           requested="cloud", model="claude-sonnet-5",
                           channel=channel)


def test_ручка_протокола_выходной_документ(monkeypatch, tmp_path):
    """Протокол — часть выходного комплекта решения: ручка отдаёт
    последний протокол файлом; без прогона сверки — честный 404."""
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", "user@example.com")
    monkeypatch.setenv("COSMOS_SKIP_CONFIG_MIGRATION", "1")
    from fastapi.testclient import TestClient

    from webapp import users
    from webapp.app import app
    monkeypatch.setattr(users, "USERS_DB_PATH",
                        tmp_path / "users.db")
    users.ensure_db()
    users.add("user@example.com", "user", by="pytest-srccheck")
    with TestClient(app) as c:
        r404 = c.get("/api/source-check/protocol")
        assert r404.status_code == 404
        assert "сверка" in r404.json()["detail"]
        snap = tmp_path / "source-check"
        snap.mkdir(parents=True)
        (snap / "protocol-last.md").write_text(
            "# Протокол\nВЫПОЛНЕНО", encoding="utf-8")
        r = c.get("/api/source-check/protocol")
        assert r.status_code == 200
        assert "ВЫПОЛНЕНО" in r.text
        assert "attachment" in r.headers["content-disposition"]


def test_пустой_ответ_читателя_отказ_словами(monkeypatch):
    monkeypatch.setattr(llm_backend, "complete",
                        _fake_complete("[]"))
    with pytest.raises(ValueError):
        scc.compile_checks(CLAUSES, scope="any", requested="cloud")
    monkeypatch.setattr(llm_backend, "complete",
                        _fake_complete("никакого массива"))
    with pytest.raises(ValueError):
        scc.compile_checks(CLAUSES, scope="any", requested="cloud")
    # то же на явном канале cli: пустой ответ / без массива — отказ
    # словами, complete (и через него API) не зовётся (S-05.3)
    monkeypatch.setattr(llm_backend, "complete", _forbidden("complete"))
    monkeypatch.setattr(llm_backend, "_api_generate",
                        _forbidden("_api_generate"))
    monkeypatch.setattr(llm_backend._settings, "has_api_key",
                        lambda: True)
    for reply in ("", "[]", "никакого массива"):
        calls: list = []
        monkeypatch.setattr(llm_backend, "_cli_generate",
                            _cli_recorder(reply, calls))
        with pytest.raises(ValueError):
            scc.compile_checks(CLAUSES, scope="any", requested="cloud",
                               model="claude-sonnet-5", channel="cli")
        assert len(calls) == 1, "одна попытка, без повтора"


# ------------------------------------------ явный канал читателя (S03)

def test_явный_cli_сильнее_ключа_и_модель_точная(monkeypatch):
    """S-05.1: при ключе API явный канал cli зовёт ровно
    _cli_generate(_SYSTEM + промпт, timeout, model=точная модель);
    complete и _api_generate не звались; в возврате наблюдённый
    канал."""
    calls: list = []
    monkeypatch.setattr(llm_backend, "_cli_generate",
                        _cli_recorder(ANSWER, calls))
    monkeypatch.setattr(llm_backend, "complete", _forbidden("complete"))
    monkeypatch.setattr(llm_backend, "_api_generate",
                        _forbidden("_api_generate"))
    monkeypatch.setattr(llm_backend._settings, "has_api_key",
                        lambda: True)
    got = scc.compile_checks(CLAUSES, scope="any", requested="cloud",
                             model="claude-sonnet-5", channel="cli")
    assert (got["backend"], got["model"], got["channel"]) == (
        "cloud", "claude-sonnet-5", "cli")
    assert calls == [(scc._SYSTEM + "\n\n" + scc.clauses_prompt(CLAUSES),
                      300.0, "claude-sonnet-5")]
    assert [s["id"] for s in got["specs"]] == ["C1"]


@pytest.mark.parametrize("failure", ["timeout", "claude exited 1",
                                     "empty response"])
def test_отказ_cli_без_подмены(monkeypatch, failure):
    """S-05.2: LLMError примитива (таймаут / ненулевой код / пустой
    вывод) → ValueError с именем канала и модели; другой канал или
    модель не пробуются."""
    calls: list = []
    monkeypatch.setattr(llm_backend, "_cli_generate",
                        _cli_recorder(llm_backend.LLMError(failure), calls))
    monkeypatch.setattr(llm_backend, "complete", _forbidden("complete"))
    monkeypatch.setattr(llm_backend, "_api_generate",
                        _forbidden("_api_generate"))
    monkeypatch.setattr(llm_backend._settings, "has_api_key",
                        lambda: True)
    with pytest.raises(ValueError) as e:
        scc.compile_checks(CLAUSES, scope="any", requested="cloud",
                           model="claude-sonnet-5", channel="cli")
    assert "cloud/claude-sonnet-5/cli" in str(e.value)
    assert failure in str(e.value)
    assert len(calls) == 1


@pytest.mark.parametrize("bad", [
    {"model": None},                  # cli без модели
    {"channel": "telepathy"},         # канал вне словаря
    {"requested": "local"},           # cli при разрешённом local
    {"channel": "api", "key": False},  # api без ключа
    {"channel": "local"},             # local при облачном контуре
])
def test_неполная_идентичность_читателя_отказ_до_транспорта(monkeypatch,
                                                            bad):
    """S-05.5 (+ S-03.4): неполная идентичность читателя — ValueError
    ДО любого транспорта."""
    kw = {"scope": "any", "requested": "cloud", "model": "claude-sonnet-5",
          "channel": "cli"}
    has_key = bad.pop("key", True)
    kw.update(bad)
    monkeypatch.setattr(llm_backend, "_cli_generate",
                        _forbidden("_cli_generate"))
    monkeypatch.setattr(llm_backend, "_api_generate",
                        _forbidden("_api_generate"))
    monkeypatch.setattr(llm_backend, "complete", _forbidden("complete"))
    monkeypatch.setattr(llm_backend._settings, "has_api_key",
                        lambda: has_key)
    with pytest.raises(ValueError):
        scc.compile_checks(CLAUSES, **kw)


def test_явный_api_точной_моделью_без_cli(monkeypatch):
    """S-03.3 (ветка api по S06): явный api зовёт _api_generate точной
    моделью с system/effort/max_tokens; _cli_generate и complete не
    звались; отказ API — словами с каналом, без ухода в CLI."""
    calls: list = []

    def _api(prompt, *, model, timeout_s, system=None, effort=None,
             temperature=None, max_tokens=None):
        calls.append((prompt, model, timeout_s, system, effort,
                      max_tokens))
        return ANSWER
    monkeypatch.setattr(llm_backend, "_api_generate", _api)
    monkeypatch.setattr(llm_backend, "_cli_generate",
                        _forbidden("_cli_generate"))
    monkeypatch.setattr(llm_backend, "complete", _forbidden("complete"))
    monkeypatch.setattr(llm_backend._settings, "has_api_key",
                        lambda: True)
    got = scc.compile_checks(CLAUSES, scope="any", requested="cloud",
                             model="claude-sonnet-5", channel="api",
                             effort="high")
    assert got["channel"] == "api" and got["model"] == "claude-sonnet-5"
    assert calls == [(scc.clauses_prompt(CLAUSES), "claude-sonnet-5",
                      300.0, scc._SYSTEM, "high", 8000)]

    def _api_fail(prompt, **kw):
        raise llm_backend.LLMError("HTTP 529")
    monkeypatch.setattr(llm_backend, "_api_generate", _api_fail)
    with pytest.raises(ValueError) as e:
        scc.compile_checks(CLAUSES, scope="any", requested="cloud",
                           model="claude-sonnet-5", channel="api")
    assert "cloud/claude-sonnet-5/api" in str(e.value)


def test_явный_local_идёт_прежним_путём_и_наблюдается(monkeypatch):
    """Канал local при local-only — прежний путь complete на локальном
    контуре; в возврате channel="local" (явное слово, не догадка)."""
    seen: list = []

    def _complete(prompt, *, backend, **kw):
        seen.append(backend)
        return {"ok": True, "text": ANSWER, "backend": backend,
                "model": "qwen-local"}
    monkeypatch.setattr(llm_backend, "complete", _complete)
    monkeypatch.setattr(llm_backend, "_cli_generate",
                        _forbidden("_cli_generate"))
    got = scc.compile_checks(CLAUSES, scope="local-only",
                             requested="local", channel="local")
    assert seen == ["local"] and got["channel"] == "local"
    assert got["model"] == "qwen-local"


# --------------------------------------------------- расход читателя ИДАК

def test_компиляция_возвращает_расход_читателя(monkeypatch):
    """Решение владельца № 1 (10.09.2026): расход читателя ИДАК — в
    токенах. Прежний путь complete отдаёт его конвертом; явный канал api
    — трубой потока (её кладёт сам _api_generate)."""
    def _complete(prompt, *, backend, **kw):
        return {"ok": True, "text": ANSWER, "backend": backend,
                "model": "модель-стенда",
                "usage": {"provider": "anthropic", "model": "модель-стенда",
                          "channel": None, "counted": True,
                          "input_tokens": 4200, "output_tokens": 130}}
    monkeypatch.setattr(llm_backend, "complete", _complete)
    got = scc.compile_checks(CLAUSES, scope="any", requested="cloud")
    assert got["usage"]["counted"] is True
    assert got["usage"]["input_tokens"] == 4200
    assert got["usage"]["output_tokens"] == 130

    # Явный канал api: конверта нет — расход снимается с трубы, которую
    # заполняет сам транспорт.
    def _api(prompt, **kw):
        llm_backend.set_last_usage(llm_backend.usage_anthropic(
            {"usage": {"input_tokens": 11, "output_tokens": 2}},
            model=kw.get("model"), channel="api"))
        return ANSWER
    monkeypatch.setattr(llm_backend, "complete", _forbidden("complete"))
    monkeypatch.setattr(llm_backend, "_api_generate", _api)
    monkeypatch.setattr(llm_backend._settings, "has_api_key", lambda: True)
    api = scc.compile_checks(CLAUSES, scope="any", requested="cloud",
                             model="модель-стенда", channel="api")
    assert api["usage"]["channel"] == "api"
    assert api["usage"]["input_tokens"] == 11


def test_канал_cli_читателя_расхода_не_даёт(monkeypatch):
    """Канал CLI подписки usage не отдаёт: строка расхода честно
    говорит «не считается» с причиной, а не показывает ноль."""
    calls: list = []
    monkeypatch.setattr(llm_backend, "complete", _forbidden("complete"))
    monkeypatch.setattr(llm_backend, "_cli_generate",
                        _cli_recorder(ANSWER, calls))
    llm_backend.set_last_usage(None)
    got = scc.compile_checks(CLAUSES, scope="any", requested="cloud",
                             model="модель-стенда", channel="cli")
    # Заглушка CLI трубу не заполняет (её заполняет настоящий примитив)
    # — читатель обязан отдать не число, а честное «расхода нет».
    assert got["usage"] is None or got["usage"]["counted"] is False
