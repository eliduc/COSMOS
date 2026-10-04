# -*- coding: utf-8 -*-
"""Сверка решений с исходными документами авиакомпании (ИДАК) —
отдельный блок у КАЖДОГО решения (слово владельца 06.09.2026):
состояние и протокол по задаче (pairing | rostering), каталог цепочек
ТЕКУЩЕГО пакета у COSMOS, ручка снимка со строками проверок."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.test_task_channel import (  # noqa: F401 — фикстуры pytest
    _channel_env, client)


# ------------------------------------------------ состояние по задаче

def _source_context(task):
    return {"task": task, "package_sha": "aa" * 32, "cosmos_run": "cosmos-test",
            "workdir": "package-runs/test", "attempt_id": "attempt-test",
            "solver_run_id": "roster-test" if task == "rostering" else None,
            "catalog_sha256": "bb" * 32 if task == "pairing" else None}


def _selected_context(monkeypatch):
    from webapp import source_check_status as scs
    monkeypatch.setattr(scs, "current_context", lambda task, **kw: _source_context(task))


def _snap(root: Path, sub: str | None, doc: dict) -> None:
    if sub:
        doc = {**_source_context(sub), **doc}
    d = root / "source-check" / sub if sub else root / "source-check"
    d.mkdir(parents=True, exist_ok=True)
    (d / "last.json").write_text(json.dumps(doc, ensure_ascii=False),
                                 encoding="utf-8")


def test_состояние_сверки_по_задаче_раздельно(tmp_path, monkeypatch):
    _selected_context(monkeypatch)
    import _data_paths
    from webapp import flow
    monkeypatch.setattr(_data_paths, "data_root", lambda: tmp_path)
    _snap(tmp_path, "pairing", {"status": "done", "reader": "r",
                                "counts": {"ok": 4, "violated": 1},
                                "run": "run-1", "at": "2026-09-06T01:00:00"})
    _snap(tmp_path, None, {"status": "running", "at": "x", "run": "run-1"})
    p = flow._source_check_state("pairing")
    assert p["status"] == "done" and p["checks"] == 5 and p["violated"] == 1
    assert flow._source_check_state("rostering") == {"status": "idle"}
    assert flow._source_check_state()["status"] == "working"


# --------------------------------------------- run_full: снимок задачи

def test_run_full_пишет_снимок_задачи_и_корневой(tmp_path, monkeypatch):
    import _data_paths
    from vmp import source_check as sc
    from vmp import source_check_run as scr
    from webapp import source_check_compile as scc
    monkeypatch.setattr(_data_paths, "data_root", lambda: tmp_path)
    monkeypatch.setattr(scr, "load_clauses",
                        lambda run_dir: [{"sid": "S1", "anchor": "S1-1",
                                          "text": "t"}])
    specs = [{"id": "C1", "kind": "duty_span_max", "anchor": "S1-1",
              "params": {}},
             {"id": "C2", "kind": "crew_size_exact", "anchor": "S1-2",
              "params": {"n": 2}}]
    compile_kw: list = []

    def _compile(*a, **kw):
        compile_kw.append(kw)
        return {"specs": specs, "problems": [], "backend": "cloud",
                "model": "m", "channel": kw.get("channel")}
    monkeypatch.setattr(scc, "compile_checks", _compile)
    seen: list = []

    def _run_checks(sp, solution, **kw):
        seen.append([s["id"] for s in sp])
        return [sc.CheckResult(check_id=s["id"], kind=s["kind"],
                               anchor=s["anchor"], verdict="ok",
                               detail="d") for s in sp]
    monkeypatch.setattr(sc, "run_checks", _run_checks)
    run_dir = tmp_path / "run-x"
    run_dir.mkdir()
    binding = {"package_sha": "ab" * 32, "revision": 3,
               "solver_run_id": None, "catalog_sha256": "c" * 64}
    got = scr.run_full(run_dir, [("пейринг (каталог цепочек)",
                                  {"flights": [], "employees": [],
                                   "flightAssignments": []},
                                  "pairing", None)],
                       requested="cloud", model="m", snap_sub="pairing",
                       channel="cli", binding=binding)
    # правило о людях (crew_size_exact) на пейринге не исполнялось
    assert seen == [["C1"]]
    assert got["counts"] == {"ok": 1}
    # канал доехал до компилятора и вернулся наблюдённым (S-04.1/S-04.2)
    assert compile_kw[0]["channel"] == "cli"
    assert got["reader_channel"] == "cli" and got["reader"] == "cloud/m"
    task_snap = json.loads((tmp_path / "source-check" / "pairing"
                            / "last.json").read_text(encoding="utf-8"))
    root_snap = json.loads((tmp_path / "source-check" / "last.json")
                           .read_text(encoding="utf-8"))
    assert task_snap["task"] == "pairing" and root_snap["task"] == "pairing"
    assert task_snap["status"] == "done"
    assert task_snap["reader_channel"] == "cli" \
        and root_snap["reader_channel"] == "cli"
    # привязка к пакету и выдаче — в снимке (§3.9 п.2)
    assert task_snap["package_sha"] == "ab" * 32 \
        and task_snap["catalog_sha256"] == "c" * 64 \
        and task_snap["revision"] == 3 and root_snap["package_sha"]
    assert task_snap["skipped_people"] == [
        {"id": "C2", "kind": "crew_size_exact", "anchor": "S1-2"}]
    assert Path(task_snap["checks"]).is_file()
    checks_doc = json.loads(Path(task_snap["checks"])
                            .read_text(encoding="utf-8"))
    assert checks_doc["reader_channel"] == "cli"
    assert (tmp_path / "source-check" / "pairing"
            / "protocol-last.md").is_file()
    # артефакты задачи — свой подкаталог, чужая задача их не затирает
    proto = (run_dir / "source-check" / "pairing" / "protocol.md")\
        .read_text(encoding="utf-8")
    assert "СУДИТСЯ НА РОСТЕРИНГЕ" in proto and "C2" in proto
    assert "читатель: cloud/m/cli" in proto
    assert task_snap["checks"] == str(run_dir / "source-check" / "pairing"
                                      / "checks.json")
    assert not (run_dir / "source-check" / "checks.json").exists()
    # второй блок (ростер) берёт компиляцию из кэша — канал в снимке
    # остаётся наблюдённым каналом ТОЙ компиляции, не выдумкой
    got2 = scr.run_full(run_dir, [("ростеринг (r1)",
                                   {"flights": [], "employees": [],
                                    "flightAssignments": []},
                                   "roster", None)],
                        requested="cloud", model="m",
                        snap_sub="rostering", channel="cli")
    assert len(compile_kw) == 1 and got2["reader_channel"] == "cli"
    r_snap = json.loads((tmp_path / "source-check" / "rostering"
                         / "last.json").read_text(encoding="utf-8"))
    assert r_snap["compiled_cached"] is True \
        and r_snap["reader_channel"] == "cli"
    # ростер: людское правило исполнялось (C1 + C2)
    assert seen[-1] == ["C1", "C2"]


def test_канал_не_указан_в_компилятор_явно_none(tmp_path, monkeypatch):
    """S-04.3 вариант (2) / замена S-05.17: канал всегда передаётся
    компилятору явно — без канала это channel=None (прежний путь
    complete), в снимке reader_channel=None, в заголовке протокола
    канала нет (показание, не выдумка)."""
    import _data_paths
    from vmp import source_check as sc
    from vmp import source_check_run as scr
    from webapp import source_check_compile as scc
    monkeypatch.setattr(_data_paths, "data_root", lambda: tmp_path)
    monkeypatch.setattr(scr, "load_clauses",
                        lambda run_dir: [{"sid": "S1", "anchor": "S1-1",
                                          "text": "t"}])
    kws: list = []

    def _compile(*a, **kw):
        kws.append(kw)
        return {"specs": [{"id": "C1", "kind": "duty_span_max",
                           "anchor": "S1-1", "params": {}}],
                "problems": [], "backend": "cloud", "model": "m",
                "channel": None}
    monkeypatch.setattr(scc, "compile_checks", _compile)
    monkeypatch.setattr(sc, "run_checks", lambda sp, sol, **kw: [])
    run_dir = tmp_path / "run-y"
    run_dir.mkdir()
    got = scr.run_full(run_dir, [("ростеринг (r1)", {}, "roster", None)],
                       requested="cloud", model="m")
    assert "channel" in kws[0] and kws[0]["channel"] is None
    assert got["reader_channel"] is None
    snap = json.loads((tmp_path / "source-check" / "last.json")
                      .read_text(encoding="utf-8"))
    assert snap["reader_channel"] is None
    proto = Path(got["protocol_path"]).read_text(encoding="utf-8")
    assert "читатель: cloud/m;" in proto


def test_компиляция_кэшируется_на_оба_решения(tmp_path, monkeypatch):
    """Второй блок (ростер) берёт проверки из кэша первого (пейринг):
    читатель зовётся один раз; иной допуск/модель — новый вызов."""
    import _data_paths
    from vmp import source_check_run as scr
    from webapp import source_check_compile as scc
    monkeypatch.setattr(_data_paths, "data_root", lambda: tmp_path)
    calls: list = []

    def _compile(clauses, **kw):
        calls.append(kw.get("model"))
        return {"specs": [{"id": "C1", "kind": "duty_span_max",
                           "anchor": "S1-1", "params": {}}],
                "problems": [], "backend": "cloud", "model": kw.get("model"),
                "channel": kw.get("channel")}
    monkeypatch.setattr(scc, "compile_checks", _compile)
    clauses = [{"sid": "S1", "anchor": "S1-1", "text": "t"}]

    def _go(cl=clauses, model="m1", channel="cli", **kw):
        return scr._cached_compile(cl, scope="any", requested="cloud",
                                   model=model, timeout_s=1,
                                   effort="medium", channel=channel, **kw)
    a = _go()
    b = _go()
    assert calls == ["m1"] and a["cached"] is False and b["cached"] is True
    assert b["specs"] == a["specs"] and b["model"] == "m1"
    assert b["channel"] == "cli", "канал из кэша — наблюдённый той компиляции"
    _go(model="m2")
    assert calls == ["m1", "m2"], "другая модель — своё прочтение"
    _go([{"sid": "S1", "anchor": "S1-1", "text": "u"}])
    assert len(calls) == 3, "другие клаузы — своё прочтение"
    assert (tmp_path / "source-check" / "compiled").is_dir()
    # S-04.4: другой канал — своё прочтение (компиляция через api не
    # удостоверяет запрос cli)
    _go(channel="api")
    assert len(calls) == 4
    _go(channel=None)
    assert len(calls) == 5
    assert _go()["cached"] is True
    # смена версии компилятора (правка промпта/словаря) обнуляет кэш
    orig_version = scc.COMPILER_VERSION
    monkeypatch.setattr(scc, "COMPILER_VERSION", "test-next")
    assert _go()["cached"] is False and len(calls) == 6
    monkeypatch.setattr(scc, "COMPILER_VERSION", orig_version)
    assert _go()["cached"] is True and len(calls) == 6
    # старый файл кэша без channel/compiler_version — пересчёт
    from webapp import llm_backend
    key = scr._compile_key(clauses, "any", "cloud", "m1", "medium",
                           channel="cli",
                           backend=llm_backend.resolve_backend("any",
                                                               "cloud"),
                           fingerprint=scc.compiler_fingerprint())
    cache = tmp_path / "source-check" / "compiled" / f"{key}.json"
    assert cache.is_file()
    cache.write_text(json.dumps({"specs": a["specs"], "key": key}),
                     encoding="utf-8")
    assert _go()["cached"] is False and len(calls) == 7
    assert json.loads(cache.read_text(encoding="utf-8"))["channel"] == "cli"
    # кривой файл кэша — пересчёт
    cache.write_text("{не json", encoding="utf-8")
    assert _go()["cached"] is False and len(calls) == 8
    # отказ читателя не кэшируется
    def _fail(clauses, **kw):
        calls.append("fail")
        raise ValueError("читатель не ответил")
    monkeypatch.setattr(scc, "compile_checks", _fail)
    with pytest.raises(ValueError):
        _go(model="m9")
    key9 = scr._compile_key(clauses, "any", "cloud", "m9", "medium",
                            channel="cli", backend="cloud",
                            fingerprint=scc.compiler_fingerprint())
    assert not (tmp_path / "source-check" / "compiled"
                / f"{key9}.json").exists()
    # допуск судится до кэша: local-only + cloud — ScopeViolation,
    # читатель не зовётся
    n = len(calls)
    with pytest.raises(llm_backend.ScopeViolation):
        scr._cached_compile(clauses, scope="local-only", requested="cloud",
                            model="m1", timeout_s=1, effort="medium",
                            channel="cli")
    assert len(calls) == n


# ----------------------------------------- каталог цепочек для сверки

def test_каталог_цепочек_сначала_у_cosmos(tmp_path, monkeypatch):
    from webapp import app as app_module, package_runs as pr
    sha = "ab" * 32
    wd = tmp_path / "package-runs" / sha
    (wd / "button-pairing").mkdir(parents=True)
    cosmos_cat = wd / "button-pairing" / "builder-catalog.json"
    cosmos_cat.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(pr, "workdir_for", lambda s: tmp_path
                        / "package-runs" / s)
    run_dir = tmp_path / "cdpm-runs" / "run-9"
    (run_dir / "button-old").mkdir(parents=True)
    (run_dir / "button-old" / "builder-catalog.json").write_text(
        "{}", encoding="utf-8")
    assert app_module._pairing_catalog_path(
        {"sha": sha}, run_dir) == cosmos_cat
    # S-05.12: отпечаток во вложенном source — тот же каталог
    assert app_module._pairing_catalog_path(
        {"source": {"sha": sha}}, run_dir) == cosmos_cat
    # каталог другого пакета не выбирается
    other = tmp_path / "package-runs" / ("ef" * 32) / "button-pairing"
    other.mkdir(parents=True)
    (other / "builder-catalog.json").write_text("{}", encoding="utf-8")
    assert app_module._pairing_catalog_path(
        {"sha": sha}, run_dir) == cosmos_cat
    # у COSMOS нажатия нет — прежний путь: каталог прогона CDPM
    assert app_module._pairing_catalog_path(
        {"sha": "cd" * 32}, run_dir) == (run_dir / "button-old"
                                         / "builder-catalog.json")
    assert app_module._pairing_catalog_path(
        {"sha": ""}, tmp_path / "нет") is None


def test_каталог_прогона_cdpm_пакет_корень_переменная(tmp_path, monkeypatch):
    """Клаузы заморозки: (1) внутри пакета, (2) cdpm-runs корня данных,
    (3) CREW_CDPM_RUNS_DIR; нет нигде — None."""
    import zipfile

    import _data_paths
    from webapp import app as app_module, package_runs as pr
    monkeypatch.setattr(_data_paths, "data_root", lambda: tmp_path / "root")
    monkeypatch.setattr(pr, "workdir_for", lambda s: tmp_path / "wd" / s)
    monkeypatch.delenv("CREW_CDPM_RUNS_DIR", raising=False)
    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("manifest.json", "{}")
    assert app_module._cdpm_run_dir("run-1", plain, {"sha": "ab" * 32}) is None
    assert app_module._cdpm_run_dir("", plain, {}) is None
    # (3) переменная площадки
    ext = tmp_path / "ext" / "run-1" / "freeze" / "clauses"
    ext.mkdir(parents=True)
    monkeypatch.setenv("CREW_CDPM_RUNS_DIR", str(tmp_path / "ext"))
    assert app_module._cdpm_run_dir("run-1", plain, {"sha": "ab" * 32}) \
        == tmp_path / "ext" / "run-1"
    # (2) корень данных сильнее переменной
    rootd = tmp_path / "root" / "cdpm-runs" / "run-1" / "freeze" / "clauses"
    rootd.mkdir(parents=True)
    assert app_module._cdpm_run_dir("run-1", plain, {"sha": "ab" * 32}) \
        == tmp_path / "root" / "cdpm-runs" / "run-1"
    # (1) клаузы в пакете сильнее всего: распаковка в каталог нажатий
    with_clauses = tmp_path / "with.zip"
    with zipfile.ZipFile(with_clauses, "w") as z:
        z.writestr("manifest.json", "{}")
        z.writestr("freeze/clauses/S1.json",
                   '{"clauses": [{"anchor": "S1-1", "text": "t"}]}')
    got = app_module._cdpm_run_dir("run-1", with_clauses, {"sha": "cd" * 32})
    assert got == tmp_path / "wd" / ("cd" * 32) / "cdpm-run" / "run-1"
    assert (got / "freeze" / "clauses" / "S1.json").is_file()
    assert got.name == "run-1", "имя прогона — в заголовке протокола"


def test_читатель_по_допуску_пакета_и_модели_сертификатора(monkeypatch):
    """S06 плана единой версии (06.09.2026): допуск «any» → облако,
    закреплённый профиль площадки (модель сертификатора) — умолчание
    (config_source=default), канал сертификатора; явная модель
    запроса — override в пределах допуска (explicit_override);
    «local-only» или допуск не объявлен/кривой → только локальная
    модель установки, наружу ничего не уходит; облачная модель при
    local-only — 400; нет модели / канал вне словаря / cli без CLI /
    api без ключа — 409 словами, без запасного провайдера."""
    from fastapi import HTTPException
    from webapp import app as app_module, llm_backend, package_runs as pr
    monkeypatch.setenv("CREW_CERTIFIER_MODEL", "claude-sonnet-5")
    monkeypatch.delenv("CREW_CERTIFIER_CHANNEL", raising=False)
    monkeypatch.setattr(llm_backend, "resolve_claude_cli",
                        lambda: "C:/claude.cmd")
    monkeypatch.setattr(llm_backend, "ollama_model", lambda: "qwen-local")
    any_pkg = {"manifest": {"kit": {"llm_scope": "any"}}}
    assert app_module._source_check_reader(any_pkg) == {
        "scope": "any", "requested": "cloud", "model": "claude-sonnet-5",
        "channel": "cli", "config_source": "default"}
    # закреплённый профиль — умолчание, не замок (S-02.2/S-05.8):
    # override в пределах допуска с записью requested/effective
    over = app_module._source_check_reader(any_pkg, "claude-opus-5")
    assert over["model"] == "claude-opus-5" \
        and over["config_source"] == "explicit_override"
    assert over["model_requested"] == "claude-opus-5" \
        and over["model_site"] == "claude-sonnet-5" \
        and over["channel"] == "cli"
    assert app_module._source_check_reader(
        any_pkg, "claude-sonnet-5")["config_source"] == "default"
    # local-only: локальная модель установки, облачный профиль не
    # строится (S-05.9); облачная модель в запросе — 400 словами
    asked: list = []
    monkeypatch.setattr(pr, "certifier_model",
                        lambda: asked.append(1) or "claude-sonnet-5")
    local = app_module._source_check_reader(
        {"manifest": {"kit": {"llm_scope": "local-only"}}})
    assert local == {"scope": "local-only", "requested": "local",
                     "model": "qwen-local", "channel": "local",
                     "config_source": "default"}
    assert not asked
    with pytest.raises(HTTPException) as e:
        app_module._source_check_reader(
            {"manifest": {"kit": {"llm_scope": "local-only"}}},
            "claude-sonnet-5")
    assert e.value.status_code == 400 and "local-only" in e.value.detail
    assert not asked, "облачный профиль при local-only не зовётся"
    # допуск не объявлен / неизвестен / кривой / не по сырому слову
    # словаря → local-only (S-02.3, S-05.7: согласовано CDPM/COSMOS/GPT)
    for kit in ({}, {"llm_scope": "unknown"}, [], "any",
                {"llm_scope": " Any "}, {"llm_scope": "ANY"},
                {"llm_scope": None}):
        got = app_module._source_check_reader({"manifest": {"kit": kit}})
        assert got["scope"] == "local-only" and got["channel"] == "local", \
            kit
    assert app_module._source_check_reader({"manifest": {}})["scope"] \
        == "local-only", "допуск не объявлен — наружу ничего не уходит"
    assert app_module._source_check_reader({})["scope"] == "local-only"
    # scope запроса, отличный от допуска пакета, — 400 (S06)
    with pytest.raises(HTTPException) as e:
        app_module._source_check_reader(any_pkg, scope="local-only")
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        app_module._source_check_reader(
            {"manifest": {"kit": {"llm_scope": "local-only"}}}, scope="any")
    assert e.value.status_code == 400
    assert app_module._source_check_reader(any_pkg, scope="any")["scope"] \
        == "any"
    # нет модели сертификатора — отказ словами, не умолчание площадки
    # (S-02.4, S-05.10)
    monkeypatch.setattr(pr, "certifier_model", lambda: None)
    with pytest.raises(HTTPException) as e:
        app_module._source_check_reader(any_pkg)
    assert e.value.status_code == 409 and "не задана" in e.value.detail
    monkeypatch.setattr(pr, "certifier_model", lambda: "claude-sonnet-5")
    # cli без агентного CLI на площадке — 409
    monkeypatch.setattr(llm_backend, "resolve_claude_cli", lambda: None)
    with pytest.raises(HTTPException) as e:
        app_module._source_check_reader(any_pkg)
    assert e.value.status_code == 409 and "CLI" in e.value.detail
    monkeypatch.setattr(llm_backend, "resolve_claude_cli",
                        lambda: "C:/claude.cmd")
    # канал вне словаря — 409 (S-02.5)
    monkeypatch.setenv("CREW_CERTIFIER_CHANNEL", "telepathy")
    with pytest.raises(HTTPException) as e:
        app_module._source_check_reader(any_pkg)
    assert e.value.status_code == 409 and "вне словаря" in e.value.detail
    # api без ключа — 409; api с ключом — допустим, channel=api
    from cdpm import providers as providers_mod
    from webapp import settings as _settings
    monkeypatch.setattr(providers_mod, "sync_cloud_key_to_env",
                        lambda: False)
    monkeypatch.setenv("CREW_CERTIFIER_CHANNEL", "api")
    monkeypatch.setattr(_settings, "has_api_key", lambda: False)
    with pytest.raises(HTTPException) as e:
        app_module._source_check_reader(any_pkg)
    assert e.value.status_code == 409 and "ключ API" in e.value.detail
    monkeypatch.setattr(_settings, "has_api_key", lambda: True)
    got = app_module._source_check_reader(any_pkg)
    assert got["channel"] == "api" and got["model"] == "claude-sonnet-5" \
        and got["requested"] == "cloud"


def test_кривой_отпечаток_пакета_отказ_словами(tmp_path):
    """S-05.13 / S-01.3: отпечаток не-hex — 409 словами, не молчаливый
    уход на легаси-каталог прогона CDPM."""
    from fastapi import HTTPException
    from webapp import app as app_module
    legacy = tmp_path / "button-1"
    legacy.mkdir()
    (legacy / "builder-catalog.json").write_text("{}", encoding="utf-8")
    with pytest.raises(HTTPException) as e:
        app_module._pairing_catalog_path({"sha": "not-a-sha!"}, tmp_path)
    assert e.value.status_code == 409 and "отпечаток" in e.value.detail
    with pytest.raises(HTTPException):
        app_module._pairing_catalog_path(
            {"source": {"sha": "ZZ" * 32}}, tmp_path)


def test_пакет_файлом_хешируется_для_каталога(tmp_path, monkeypatch):
    """S-05.14 / S-01.2: пакет «файлом руками» (без sha в канале)
    находит каталог нажатий COSMOS по хешу файла; тот же приём даёт
    отпечаток для привязки снимка; файл не хешируется — 409."""
    from fastapi import HTTPException
    from webapp import app as app_module, package_runs as pr
    sha = "ef" * 32
    cat = (tmp_path / "package-runs" / sha / "button-pairing"
           / "builder-catalog.json")
    cat.parent.mkdir(parents=True)
    cat.write_text("{}", encoding="utf-8")
    zip_path = tmp_path / "file-selected.zip"
    zip_path.write_bytes(b"stub")
    hashed: list = []
    monkeypatch.setattr(pr, "_sha256", lambda p: hashed.append(p) or sha)
    monkeypatch.setattr(pr, "workdir_for",
                        lambda s: tmp_path / "package-runs" / s)
    cur = {"path": str(zip_path), "source": {"kind": "file"}}
    assert app_module._pairing_catalog_path(cur, tmp_path / "нет") == cat
    assert hashed == [zip_path]
    assert app_module._current_package_sha(cur, zip_path) == sha
    assert app_module._current_package_sha({"sha": "ab" * 32},
                                           zip_path) == "ab" * 32
    assert app_module._current_package_sha({}, None) == ""

    def _boom(p):
        raise OSError("нет доступа")
    monkeypatch.setattr(pr, "_sha256", _boom)
    with pytest.raises(HTTPException) as e:
        app_module._pairing_catalog_path(cur, tmp_path)
    assert e.value.status_code == 409 and "не хешируется" in e.value.detail


def test_читатель_пакета_видит_вложенные_клаузы(tmp_path):
    """Дополнение к 1.17 (06.09.2026): manifest.files называет клаузы
    заморозки с путём freeze/clauses/<sid>.json — читатель пакета отдаёт
    их и по полному имени, и по короткому; корень остаётся плоским."""
    import zipfile

    from webapp import task_package as tp
    z = tmp_path / "p.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("manifest.json", "{}")
        zf.writestr("freeze/clauses/S1.json", '{"clauses": []}')
        zf.writestr("freeze/clauses/S2.json", '{"clauses": [1]}')
    m = tp._read_members(z)
    assert m["manifest.json"] == b"{}"
    assert m["freeze/clauses/S1.json"] == b'{"clauses": []}'
    assert m["S2.json"] == b'{"clauses": [1]}'
    # каталог вместо zip — те же имена
    d = tmp_path / "pkg"
    (d / "freeze" / "clauses").mkdir(parents=True)
    (d / "manifest.json").write_text("{}", encoding="utf-8")
    (d / "freeze" / "clauses" / "S1.json").write_text("[]", encoding="utf-8")
    md = tp._read_members(d)
    assert md["freeze/clauses/S1.json"] == b"[]" and md["S1.json"] == b"[]"
    # короткое имя корня сильнее одноимённой вложенной записи
    z2 = tmp_path / "p2.zip"
    with zipfile.ZipFile(z2, "w") as zf:
        zf.writestr("freeze/clauses/manifest.json", "nested")
        zf.writestr("manifest.json", "root")
    assert tp._read_members(z2)["manifest.json"] == b"root"


def test_задача_сверки_проверяется_словами():
    from fastapi import HTTPException
    from webapp import app as app_module
    assert app_module._source_check_task(None) is None
    assert app_module._source_check_task(" Pairing ") == "pairing"
    with pytest.raises(HTTPException) as e:
        app_module._source_check_task("roster")
    assert e.value.status_code == 400


# ------------------------------------------------- ручки снимка/протокола

def test_ручка_снимка_отдаёт_строки_проверок(client, tmp_path, monkeypatch):
    _selected_context(monkeypatch)
    import _data_paths
    monkeypatch.setattr(_data_paths, "data_root", lambda: tmp_path)
    r = client.get("/api/source-check/last?task=pairing")
    assert r.status_code == 200 and r.json()["status"] == "idle"
    checks = tmp_path / "run-1" / "source-check" / "checks.json"
    checks.parent.mkdir(parents=True)
    checks.write_text(json.dumps({"problems": ["p1"], "results": {
        "пейринг (каталог цепочек)": [
            {"check_id": "C1", "kind": "duty_span_max", "anchor": "S1-1",
             "verdict": "ok", "detail": "42 смен", "objects_seen": 42,
             "violations": []},
            {"check_id": "C3", "kind": "rest_min", "anchor": "S1-3",
             "verdict": "violated", "detail": "x", "objects_seen": 5,
             "violations": [{"a": 1}, {"a": 2}]}]}},
        ensure_ascii=False), encoding="utf-8")
    _snap(tmp_path, "pairing", {
        "status": "done", "at": "2026-09-06T01:00:00", "run": "run-1",
        "reader": "cli/m", "counts": {"ok": 1, "violated": 1},
        "checks": str(checks), "protocol": str(checks.with_name("protocol.md")),
        "skipped_people": [{"id": "C2", "kind": "crew_size_exact",
                            "anchor": "S1-2"}]})
    checks.with_name("protocol.md").write_text(
        "# протокол", encoding="utf-8")
    r = client.get("/api/source-check/last?task=pairing")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "done" and d["task"] == "pairing"
    assert [x["id"] for x in d["rows"]] == ["C1", "C3"]
    assert d["rows"][1]["violations"] == 2
    assert d["problems_list"] == ["p1"]
    assert d["skipped_people"][0]["id"] == "C2"
    # протокол по задаче
    r2 = client.get("/api/source-check/protocol?task=pairing")
    assert r2.status_code == 200 and r2.text.startswith("# протокол")
    assert client.get("/api/source-check/protocol?task=rostering")\
        .status_code == 404
    assert client.get("/api/source-check/last?task=roster")\
        .status_code == 400


def test_запуск_сверки_по_задаче_требует_решение(client, tmp_path,
                                                monkeypatch):
    """task=pairing без каталога цепочек и task=rostering без номера
    прогона — отказ словами (409), фон не стартует."""
    import _data_paths
    from webapp import app as app_module, task_channel, task_package
    monkeypatch.setattr(_data_paths, "data_root", lambda: tmp_path)
    run_dir = tmp_path / "cdpm-runs" / "run-7" / "freeze" / "clauses"
    run_dir.mkdir(parents=True)
    pkg = tmp_path / "p.zip"
    pkg.write_bytes(b"stub")
    monkeypatch.setattr(task_channel, "current_package",
                        lambda: {"path": str(pkg), "sha": "ef" * 32})
    monkeypatch.setattr(task_package, "load", lambda path, **kw: {
        "manifest": {"source_run": "run-7"}})
    monkeypatch.setattr(app_module, "_pairing_catalog_path",
                        lambda cur, rd: None)
    r = client.post("/api/source-check/run", json={"task": "pairing"})
    assert r.status_code == 409 and "Пейринг" in r.json()["detail"]
    r = client.post("/api/source-check/run", json={"task": "rostering"})
    assert r.status_code == 409 and "Ростеринг" in r.json()["detail"]
    r = client.post("/api/source-check/run", json={"task": "crew"})
    assert r.status_code == 400


# ------------------------------------ ручка запуска: счастливый путь

def _handler_env(tmp_path, monkeypatch, kit_scope="any"):
    """Обстановка счастливого пути ручки запуска (S-05.15): пакет с
    допуском kit_scope, каталог прогона с клаузами, каталог цепочек
    нажатия «Пейринг» ТЕКУЩЕГО пакета у COSMOS, решение ростеринга с
    решателя, читатель cli площадки; фон — сразу, run_full — запись
    вызова."""
    import threading

    import httpx

    import _data_paths
    from vmp import source_check_run as scr
    from webapp import (app as app_module, llm_backend, package_runs as pr,
                        task_channel, task_package)
    monkeypatch.setattr(_data_paths, "data_root", lambda: tmp_path)
    monkeypatch.setenv("CREW_CERTIFIER_MODEL", "claude-sonnet-5")
    monkeypatch.delenv("CREW_CERTIFIER_CHANNEL", raising=False)
    monkeypatch.setattr(llm_backend, "resolve_claude_cli",
                        lambda: "C:/claude.cmd")
    run_dir = tmp_path / "cdpm-runs" / "run-0011"
    (run_dir / "freeze" / "clauses").mkdir(parents=True)
    pkg = tmp_path / "p.zip"
    pkg.write_bytes(b"stub")
    sha = "ab" * 32
    cat = (tmp_path / "package-runs" / sha / "button-pairing"
           / "builder-catalog.json")
    cat.parent.mkdir(parents=True)
    cat.write_text(json.dumps({"marker": "current"}), encoding="utf-8")
    # SOURCE-REST-R1: ведомость входа построителя рядом с каталогом —
    # источник границы «стыковка / отдых» пейринга: без правила
    # максимальной стыковки кнопка --sit-max не подаёт, построитель режет
    # по полу отдыха станции (--rest-base / --rest-off; решение владельца
    # 06.09, уточнение GPT-053)
    (cat.parent / "builder-input.json").write_text(json.dumps({
        "args": ["--duty-span", "720", "--rest-base", "720",
                 "--rest-off", "600"],
        "provenance": {"--sit-max": {
            "value": None, "passed": False, "basis": "rest_floor",
            "boundary_min": 600,
            "from": "выведено из пола минимального отдыха 600 мин "
                    "(правило VN-024; min_rest_outstation_min=600)"}}},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(task_channel, "current_package",
                        lambda: {"path": str(pkg), "sha": sha,
                                 "run": {"run": "cosmos-test",
                                         "workdir": "package-runs/" + sha}})
    monkeypatch.setattr(task_package, "load", lambda path, **kw: {
        "manifest": {"source_run": "run-0011", "revision": 3,
                     "kit": {"llm_scope": kit_scope}}})
    monkeypatch.setattr(app_module, "_cdpm_run_dir",
                        lambda sr, p, cur=None: run_dir
                        if sr == "run-0011" else None)
    monkeypatch.setattr(pr, "workdir_for",
                        lambda s: tmp_path / "package-runs" / s)
    monkeypatch.setattr(pr, "solver_url", lambda: "http://solver.invalid")
    # The endpoint consumes selected identity; its resolver has pure tests.
    from webapp import source_check_status as scs
    def _context(task, **kw):
        return {**_source_context(task), "package_sha": sha,
                "workdir": "package-runs/" + sha,
                "solver_run_id": roster["runId"] if task == "rostering" else None,
                "catalog_sha256": pr._sha256(cat) if task == "pairing" else None}
    monkeypatch.setattr(scs, "current_context", _context)
    monkeypatch.setattr(scr, "clean_flights_from_package",
                        lambda p: {"F1": {}})
    monkeypatch.setattr(scr, "catalog_to_solution",
                        lambda catalog, fmap: {"catalog": catalog})
    calls: list = []
    monkeypatch.setattr(scr, "run_full",
                        lambda *a, **kw: calls.append((a, kw)) or {})
    roster = {"runId": "cd2677e1-078b-49b6-ad84-9c310e82125f"}
    # показания ИМЕННО этого прогона (applied-rules): пакетный прогон
    # без ручки max_sit_duration — day_break_threshold_min=0, граница
    # выводится из пола минимального отдыха тех же показаний (600)
    applied = {"runId": roster["runId"],
               "lastRun": {"profileReadings": {
                   "day_break_threshold_min": 0, "min_rest_home_min": 720,
                   "min_rest_outstation_min": 600}}}
    gets: list = []

    class _Resp:
        status_code = 200
        def __init__(self, url):
            self.url = url

        def raise_for_status(self):
            pass

        def json(self):
            return applied if self.url.endswith("/applied-rules") else roster
    monkeypatch.setattr(httpx, "get",
                        lambda url, **kw: gets.append((url, kw)) or _Resp(url))

    class _Now:
        def __init__(self, target, daemon=False):
            self.target = target

        def start(self):
            self.target()
    monkeypatch.setattr(threading, "Thread", _Now)
    return {"run_dir": run_dir, "cat": cat, "sha": sha, "roster": roster,
            "calls": calls, "gets": gets}


def test_запуск_сверки_совмещает_каталог_и_точный_ростер(client, tmp_path,
                                                         monkeypatch):
    """S-05.15 (совмещённый запуск без task): 202 с pairing/rostering и
    запрошенным читателем; run_full получил каталог прогона, решения
    [пейринг текущего пакета, ростер точного ID], читателя any/cloud/
    claude-sonnet-5/cli, snap_sub=None и привязку к пакету/выдаче;
    решение ростера — с решателя по точному номеру."""
    env = _handler_env(tmp_path, monkeypatch)
    rid = env["roster"]["runId"]
    r = client.post("/api/source-check/run", json={"solverRunId": rid})
    assert r.status_code == 202, r.text
    d = r.json()
    assert d["pairing"] is True and d["rostering"] is True \
        and d["task"] is None and d["run"] == "run-0011"
    assert d["reader_requested"] == {
        "scope": "any", "requested": "cloud", "model": "claude-sonnet-5",
        "channel": "cli", "config_source": "default"}
    assert d["package_sha"] == env["sha"]
    (args, kw), = env["calls"]
    assert args[0] == env["run_dir"]
    assert [s[2] for s in args[1]] == ["pairing", "roster"]
    assert args[1][0][1] == {"catalog": {"marker": "current"}}
    assert args[1][1][1] == env["roster"] and rid[:8] in args[1][1][0]
    assert {k: kw[k] for k in ("scope", "requested", "model", "channel",
                               "snap_sub")} == {
        "scope": "any", "requested": "cloud", "model": "claude-sonnet-5",
        "channel": "cli", "snap_sub": None}
    b = kw["binding"]
    assert b["package_sha"] == env["sha"] and b["solver_run_id"] == rid
    assert b["revision"] == 3 and b["catalog_path"] == str(env["cat"])
    assert len(b["catalog_sha256"]) == 64
    # SOURCE-REST-R1: решение и показания — ИМЕННО этого runId
    assert env["gets"] == [
        (f"http://solver.invalid/schedules/run/{rid}/result",
         {"timeout": 120.0}),
        (f"http://solver.invalid/schedules/run/{rid}/applied-rules",
         {"timeout": 120.0})]
    # граница «стыковка / отдых» T — по каждому решению из артефактов
    # прогона по одному определению (vmp.sit_boundary): пейринг —
    # --sit-max ведомости построителя, ростер — показания прогона
    # (ручки нет → пол минимального отдыха 600, провенанс «выведено»)
    pb, rb = args[1][0][4], args[1][1][4]
    assert pb["rest_boundary_min"] == 600 \
        and "выведено из пола отдыха построителя" in pb["rest_boundary_source"] \
        and "VN-024" in pb["rest_boundary_source"] \
        and pb["rest_boundary_basis"] == "rest_floor" \
        and pb["rest_boundary_by_place"] == {"home": 720, "outstation": 600}
    assert rb["rest_boundary_min"] == 600 \
        and "выведено из пола минимального отдыха 600 мин" \
        in rb["rest_boundary_source"] \
        and "day_break_threshold_min=0" in rb["rest_boundary_source"] \
        and rid in rb["rest_boundary_source"] \
        and rb["rest_boundary_basis"] == "rest_floor" \
        and "МОДЕЛЬНОЕ УПРОЩЕНИЕ" in rb["rest_boundary_assumption"]
    # снимок running несёт запрошенного читателя и отпечаток пакета
    # (S-01.4; run_full здесь заглушка — снимок не перезаписан)
    last = json.loads((tmp_path / "source-check" / "last.json")
                      .read_text(encoding="utf-8"))
    assert last["status"] == "running" and last["run"] == "run-0011"
    assert last["reader_requested"]["channel"] == "cli" \
        and last["package_sha"] == env["sha"]


def test_запуск_сверки_по_задаче_пейринг_и_ростер(client, tmp_path,
                                                  monkeypatch):
    """Варианты по задаче (требование аудита): pairing — solverRunId
    игнорируется, snap_sub="pairing", решатель не опрашивается;
    rostering — точный ID, snap_sub="rostering", каталог не читается;
    сбой фона — снимок error с читателем; отказ читателя — до фона."""
    env = _handler_env(tmp_path, monkeypatch)
    rid = env["roster"]["runId"]
    r = client.post("/api/source-check/run",
                    json={"task": "pairing", "solverRunId": rid})
    assert r.status_code == 202, r.text
    assert r.json()["pairing"] is True and r.json()["rostering"] is False
    args, kw = env["calls"][-1]
    assert [s[2] for s in args[1]] == ["pairing"]
    assert kw["snap_sub"] == "pairing" \
        and kw["binding"]["solver_run_id"] is None
    assert env["gets"] == []
    r = client.post("/api/source-check/run",
                    json={"task": "rostering", "solverRunId": rid})
    assert r.status_code == 202, r.text
    assert r.json()["pairing"] is False and r.json()["rostering"] is True
    args, kw = env["calls"][-1]
    assert [s[2] for s in args[1]] == ["roster"]
    assert kw["snap_sub"] == "rostering" \
        and kw["binding"]["catalog_path"] is None \
        and kw["binding"]["solver_run_id"] == rid
    assert len(env["gets"]) == 2       # result + applied-rules этого runId
    last = json.loads((tmp_path / "source-check" / "rostering"
                       / "last.json").read_text(encoding="utf-8"))
    assert last["task"] == "rostering" \
        and last["reader_requested"]["model"] == "claude-sonnet-5"
    # сбой фона — снимок error тоже несёт запрошенного читателя
    from vmp import source_check_run as scr

    def _boom(*a, **kw):
        raise RuntimeError("читатель упал")
    monkeypatch.setattr(scr, "run_full", _boom)
    r = client.post("/api/source-check/run", json={"solverRunId": rid})
    assert r.status_code == 202
    last = json.loads((tmp_path / "source-check" / "last.json")
                      .read_text(encoding="utf-8"))
    assert last["status"] == "error" and "читатель упал" in last["error"]
    assert last["reader_requested"]["channel"] == "cli"
    # отказ читателя — до фона (модель площадки не задана → 409 словами)
    monkeypatch.delenv("CREW_CERTIFIER_MODEL")
    n = len(env["calls"])
    r = client.post("/api/source-check/run", json={"solverRunId": rid})
    assert r.status_code == 409 and "не задана" in r.json()["detail"]
    assert len(env["calls"]) == n
    monkeypatch.setenv("CREW_CERTIFIER_MODEL", "claude-sonnet-5")
    # scope запроса не по допуску пакета — 400 словами, до фона
    r = client.post("/api/source-check/run",
                    json={"solverRunId": rid, "scope": "local-only"})
    assert r.status_code == 400 and len(env["calls"]) == n
    # override модели — в пределах допуска, с записью источника
    monkeypatch.setattr(scr, "run_full",
                        lambda *a, **kw: env["calls"].append((a, kw)) or {})
    r = client.post("/api/source-check/run",
                    json={"task": "pairing", "model": "claude-opus-5"})
    assert r.status_code == 409, "свежий running задачи pairing — гейт"
    # Единая версия 06.09.2026 (живой урок раската): running ЧУЖОГО
    # процесса — рестарт приложения посреди сверки — брошенный фон,
    # не гейт; свой pid остаётся гейтом (проверено строкой выше)
    _pl = tmp_path / "source-check" / "pairing" / "last.json"
    _doc = json.loads(_pl.read_text(encoding="utf-8"))
    assert _doc["server_pid"] == os.getpid()
    _doc["server_pid"] = os.getpid() + 100000
    _pl.write_text(json.dumps(_doc, ensure_ascii=False), encoding="utf-8")
    r = client.post("/api/source-check/run",
                    json={"task": "pairing", "model": "claude-opus-5"})
    assert r.status_code == 202, "running чужого pid — брошенный фон, не гейт"
    r = client.post("/api/source-check/run",
                    json={"solverRunId": rid, "model": "claude-opus-5"})
    assert r.status_code == 202
    assert r.json()["reader_requested"]["config_source"] \
        == "explicit_override"
    assert env["calls"][-1][1]["model"] == "claude-opus-5"
