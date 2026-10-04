# -*- coding: utf-8 -*-
"""Паспорт прогона COSMOS = рецепт воспроизведения (webapp/cosmos_runs:
environment / inputs_of / attempt_recipe / passport_of / summary_of;
раздел 8 проекта прогонов; слова владельца 07.09.2026: «прогон должен
хранить также модели и солверы, которые использовались»).

Охраняемое: окружение при рождении кэшируется по ключу настроек и
сбрасывается forget(); паспорт — только чтение с диска: чего нет —
null с кодом причины (pre-passport, not-declared-by-package,
catalog-unavailable, no-build-files, snapshot-of-other-package,
package-bytes-missing), ничего не выдумывается; параметры старых
попыток реконструируются по артефактам с перечнем params_reconstructed;
читатель ИДАК приписывается попытке только при совпадении пакета и
прогона; сводка для списка — коды состояний."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from tests.test_task_channel import _pkg_bytes  # noqa: E402

#: Адрес решателя для стендов — вымышленный (зона `.invalid`
#: закреплена RFC 6761 и не разрешается никогда). Паспорт судится
#: по ПЕРЕНОСУ и ОЧИСТКЕ адреса, а не по связи: живого решателя
#: здесь нет, и зависеть от того, что слушает порт на машине,
#: стенд не вправе.
SOLVER_URL = "http://solver.invalid"
#: Тот же адрес с учётными данными: очистка обязана их снять.
SOLVER_URL_SECRET = "http://u:p@solver.invalid"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data-root"))
    for k in ("CREW_TASK_INBOX", "CREW_TASK_PACKAGE", "CREW_VMP_REGISTRY",
              "CREW_SOLVER_URL", "CREW_COSMOS_RUNS_DIR",
              "CREW_CERTIFIER_MODEL", "CREW_CERTIFIER_CHANNEL"):
        monkeypatch.delenv(k, raising=False)
    from webapp import cosmos_runs, flow, task_channel
    monkeypatch.setitem(flow._READY_CACHE, "key", None)
    monkeypatch.setitem(task_channel._CARD_CACHE, "key", None)
    cosmos_runs.forget()


def _receive(tmp_path: Path) -> tuple[str, dict]:
    from webapp import task_channel
    body = _pkg_bytes(tmp_path / "pkg")
    sha = hashlib.sha256(body).hexdigest()
    code, brief = task_channel.receive(body, sha)
    assert code == 200, brief
    return sha, brief


def _wj(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def _rec(aid: str, task: str = "pairing", **kw) -> dict:
    rec = {"id": aid, "task": task, "pairing_selection": None,
           "engine": "cpsat" if task == "pairing" else None,
           "started_at": "2026-09-07T10:00:00", "state": "завершено",
           "done": True, "verdict": "пейринг построен" if task == "pairing"
           else "допустимо", "error": None,
           "finished_at": "2026-09-07T10:05:00",
           "catalog": "attempts/%s/%s" % (aid, "button-pairing"
                                          if task == "pairing" else "button")}
    rec.update(kw)
    return rec


def _state(wd: Path, attempts: list, **extra) -> None:
    doc = {"attempts": attempts, "done": True}
    doc.update(extra)
    _wj(wd / "state.json", doc)


# ---------------------------------------------------------- окружение

def test_environment_cached_by_key_and_forget(monkeypatch):
    from webapp import cosmos_runs as cr
    env1 = cr.environment()
    assert env1 == cr.environment()
    assert env1 is not cr.environment(), "копия, не общий словарь"
    assert env1["solver"]["url"] is None
    monkeypatch.setenv("CREW_SOLVER_URL", SOLVER_URL)
    env2 = cr.environment()
    assert env2["solver"]["url"] == SOLVER_URL
    monkeypatch.setenv("CREW_CERTIFIER_MODEL", "claude-sonnet-5")
    assert cr.environment()["certifier_setting"] == {"model": "claude-sonnet-5",
                                                    "channel": "cli"}
    calls = {"n": 0}
    real = cr._solver_environment

    def counting():
        calls["n"] += 1
        return real()
    monkeypatch.setattr(cr, "_solver_environment", counting)
    cr.environment()
    cr.environment()
    assert calls["n"] == 0, "кэш по ключу настроек"
    cr.forget()
    cr.environment()
    assert calls["n"] == 1


def test_environment_solver_url_carries_no_userinfo_and_run_json_has_no_secret(
        tmp_path, monkeypatch):
    """Секрет в паспорте: CREW_SOLVER_URL с user:pass@ — в паспорт
    окружения адрес идёт без учётных данных (как в записи попытки и в
    манифесте журнала), а run.json рождённого прогона их не содержит;
    ключ кэша окружения — по сырому значению (смена пароля — новый ключ)."""
    from webapp import cosmos_runs as cr
    monkeypatch.setenv("CREW_SOLVER_URL", SOLVER_URL_SECRET)
    cr.forget()
    env = cr.environment()
    assert env["solver"]["url"] == SOLVER_URL
    assert "u:p@" not in json.dumps(env)
    got = cr.create_run("секрет")
    raw = (tmp_path / "data-root" / "cosmos-runs" / got["run"] / "run.json") \
        .read_text(encoding="utf-8")
    assert "u:p@" not in raw and SOLVER_URL in raw
    card = cr.run_card(got["run"])
    assert "u:p@" not in json.dumps(card, ensure_ascii=False)
    assert "u:p@" not in json.dumps(cr.list_runs(), ensure_ascii=False)
    # смена учётных данных при том же хосте — другой ключ кэша, тот же
    # очищенный адрес
    monkeypatch.setenv("CREW_SOLVER_URL", "http://u:p2@solver.invalid")
    assert cr.environment()["solver"]["url"] == SOLVER_URL


def test_environment_has_code_solvers_catalog_and_codes():
    from webapp import cosmos_runs as cr
    env = cr.environment()
    assert env["product"] == "COSMOS"
    assert set(env["code"]) >= {"commit", "commit_short", "tree_dirty", "dirty_paths"}
    assert env["python"] and env["platform"]
    assert set(env["packages"]) == set(cr.PACKAGES_OF_INTEREST)
    assert env["packages"]["ortools"], "ortools установлен на площадке"
    sol = env["solver"]
    assert sol["engine"] == "timefold" and sol["version_endpoint"] is None
    assert sol["version_endpoint_code"] == "no-version-endpoint"
    if sol["version_source_code"] == "build-files":
        assert set(sol["build"]) == {"launcher_jar_sha256", "app_jar_sha256",
                                     "manifest_commit_short", "matches_manifest",
                                     "timefold_version", "quarkus_version",
                                     "config_sha256"}
    else:
        assert sol["build"] is None and sol["version_source_code"] == "no-build-files"
    assert "cpsat" in env["pairing_engines"]["available"]
    assert env["catalog"]["version"] and env["catalog"]["fingerprint"]
    assert env["certifier_setting"] == {"model": None, "channel": "cli"}
    assert isinstance(env["attempts_keep"], int)


def test_build_identity_carries_solver_build_fields():
    from webapp import build_identity as bi
    ident = bi.build_identity(refresh=True)
    sol = ident["solver"]
    for key in ("app_jar_sha256", "timefold_version", "quarkus_version",
                "versions_source", "config_sha256"):
        assert key in sol, key
    if sol["versions_source"] == "dependencies":
        assert sol["timefold_version"] and sol["quarkus_version"]
    assert sol["jar_sha256"] == bi.build_identity()["solver"]["jar_sha256"]


def test_passport_names_catalog_unavailable(monkeypatch):
    from vmp import catalog_access as ca
    from webapp import cosmos_runs as cr

    def boom():
        raise RuntimeError("каталог шаблонов не найден: /нет")
    monkeypatch.setattr(ca, "stamp", boom)
    cr.forget()
    env = cr.environment()
    assert env["catalog"]["version"] is None
    assert env["catalog"]["reason_code"] == "catalog-unavailable"
    assert "каталог шаблонов не найден" in env["catalog"]["error"]


def test_no_build_files_named_when_solver_files_absent(monkeypatch, tmp_path):
    from webapp import build_identity as bi, cosmos_runs as cr
    monkeypatch.setattr(bi, "KOTLIN_JAR", tmp_path / "нет.jar")
    monkeypatch.setattr(bi, "KOTLIN_APP_DIR", tmp_path / "нет")
    monkeypatch.setattr(bi, "KOTLIN_DEPS", tmp_path / "нет.txt")
    monkeypatch.setattr(bi, "KOTLIN_POM", tmp_path / "нет.xml")
    monkeypatch.setattr(bi, "KOTLIN_CONFIG", tmp_path / "нет.properties")
    monkeypatch.setattr(bi, "_cache", None)
    cr.forget()
    sol = cr.environment()["solver"]
    assert sol["build"] is None and sol["version_source_code"] == "no-build-files"
    monkeypatch.setattr(bi, "_cache", None)


# ------------------------------------------------------- рождение, входы

def test_run_birth_writes_environment_and_card_exposes_passport(tmp_path):
    from webapp import cosmos_runs as cr
    doc = cr.create_run("Демо")
    assert doc["passport"]["environment"]["product"] == "COSMOS"
    assert doc["passport"]["at"] == doc["created_at"]
    card = cr.run_card(doc["run"])
    p = card["passport"]
    assert p["created"] == doc["passport"]["environment"]
    assert p["created_at"] == doc["created_at"]
    assert p["inputs"] == [] and p["attempts"] == []
    assert p["journals"] == {"attempts_with_exchanges": 0, "model_exchanges": 0,
                             "source_check_snapshots": 0}
    assert p["attempts_trimmed"] == 0
    assert "pre-passport" not in p["gaps"]
    assert card["passport_summary"] if "passport_summary" in card else True
    s = cr.summary_of(doc)
    assert s["state_code"] == "no-attempts" and s["solvers"] == []
    assert s["code"] == doc["passport"]["environment"]["code"]["commit_short"]
    assert s["catalog"] == doc["passport"]["environment"]["catalog"]["version"]


def test_inputs_from_manifest_as_is_and_made_by_null_with_code(tmp_path):
    from webapp import cosmos_runs as cr
    sha, brief = _receive(tmp_path)
    doc = cr.read_run(brief["cosmos_run"])
    inputs = cr.inputs_of(doc)
    assert len(inputs) == 1
    inp = inputs[0]
    assert inp["sha256"] == sha and len(inp["manifest_sha256"]) == 64
    assert inp["source_run"] == "run-9001" and inp["spec_version"]
    assert inp["revision"] == 2 and inp["customer"] == "Т"
    assert inp["made_by"] is None and inp["made_by_code"] == "not-declared-by-package"
    assert inp["catalog"] is None and inp["catalog_code"] == "not-declared-by-package"
    names = {f["name"] for f in inp["files"]}
    assert {"model.json", "clean-data.json"} <= names
    assert all(len(f["sha256"]) == 64 and f["bytes"] > 0 for f in inp["files"])
    assert inp["files_count"] == len(inp["files"])
    assert inp["verdict"] == "OK" and inp["mg"]["pairing"]["state"]
    assert inp["note_code"] is None
    # made_by / kit / data_fingerprint — из манифеста как есть
    entry = cr._package_entry(
        "a" * 64, Path("x.zip"), source="channel", brief=None,
        manifest={"made_by": {"product": "CDPM", "code": "abc",
                              "models": [{"role": "extraction",
                                          "model": "test-stub"}]},
                  "kit": {"data_class": "synthetic", "llm_scope": "any"},
                  "data_fingerprint": {"sha256": "d" * 64},
                  "files": [{"name": "a"}], "revision": 3, "customer": "К",
                  "source_run": "run-0001", "spec_version": "1.18"},
        manifest_sha256="m" * 64, at="2026-09-07T12:00:00+03:00")
    assert entry["made_by"]["models"][0]["model"] == "test-stub"
    assert entry["kit"] == {"data_class": "synthetic", "llm_scope": "any"}
    assert entry["data_fingerprint"] == {"sha256": "d" * 64}
    assert entry["files_count"] == 1 and entry["revision"] == 3
    assert entry["customer"] == "К" and entry["source_run"] == "run-0001"
    doc2 = dict(doc, packages=[dict(entry, workdir="package-runs/" + "a" * 64)],
                current_package="a" * 64)
    inp2 = cr.inputs_of(doc2)[0]
    assert inp2["made_by_code"] is None and inp2["made_by"]["product"] == "CDPM"
    assert inp2["note_code"] == "package-bytes-missing"   # x.zip нет
    summary = cr.summary_of(doc2, attempts=[])
    assert summary["models"]["cdpm"] == "extraction: test-stub"
    assert "not-declared-by-package" not in cr.passport_of(doc2, [])["gaps"]
    assert "package-bytes-missing" in cr.passport_of(doc2, [])["gaps"]


# ------------------------------------------------------- рецепт попытки

def test_attempt_recipe_reconstructs_old_pairing_params_from_builder_input_and_names_gaps(
        tmp_path):
    from webapp import cosmos_runs as cr
    sha, brief = _receive(tmp_path)
    wd = tmp_path / "data-root" / "package-runs" / sha
    _state(wd, [_rec("A1")])
    out = wd / "attempts" / "A1" / "button-pairing"
    _wj(out / "delivery.json", {
        "verdict": "пейринг построен", "mode": "feasibility-hard",
        "certified": True, "certificate_gate": [], "findings": [],
        "uncovered": [], "chains": 5, "catalog_sha256": "c" * 64,
        "coverage": {"complete": True},
        "schedule": {"solver_run_id": "r-p-1"},
        "trace": {"package_fingerprint": "m" * 64}})
    _wj(out / "builder-input.json", {"args": ["--budget-s", "240",
                                              "--workers", "8"],
                                     "provenance": {"engine": "cpsat"}})
    _wj(out / "builder-catalog.json", {"selected": [{"fns": ["1"]}],
                                       "ip_status": "OPTIMAL",
                                       "solver_status": "OPTIMAL",
                                       "config_sig": {"a": 1}})
    _wj(out / "certificate.json", {"certifier": {"scope": "any",
                                                 "backend": "cloud",
                                                 "channel": "cli",
                                                 "model": "claude-sonnet-5"},
                                   "certificate": {"counters": {"checked": 3}}})
    (out / "select-1-cplex.log").write_text("IBM ILOG CPLEX Optimization Studio "
                                            "22.1.1 banner\n", encoding="utf-8")
    doc = cr.read_run(brief["cosmos_run"])
    rec = cr.attempt_recipe(wd, _rec("A1", engine="cplex"), doc["packages"][0])
    assert rec["attempt"] == {"id": "A1", "task": "pairing", "kind": "pairing",
                              "started_at": "2026-09-07T10:00:00",
                              "finished_at": "2026-09-07T10:05:00",
                              "state": "завершено",
                              "verdict": "пейринг построен", "run": None,
                              "journal": {"present": False,
                                          "reason_code": "pre-journal"},
                              "replay": {"of": None},
                              # актора у попытки прежней версии нет —
                              # рецепт молчит, а не выдумывает нажавшего
                              "actor": None}
    assert rec["package"] == {"sha": sha, "manifest_sha256": "m" * 64,
                              "source_run": "run-9001"}
    assert rec["params"] == {"spent_limit_s": 240, "mode": "feasibility-hard"}
    assert rec["params_reconstructed"] == ["spent_limit_s", "mode"]
    assert rec["params_code"] == "pre-passport"
    assert rec["reproducibility"] == {"class": None, "reason_code": "pre-passport"}
    p = rec["pairing"]
    assert p["select_engine"] == "cplex" and p["cplex"] == {"version": "22.1.1"}
    assert p["args"] == ["--budget-s", "240", "--workers", "8"]
    assert p["provenance"] == {"engine": "cpsat"} and p["config_sig"] == {"a": 1}
    assert p["status"] == {"solver_status": "OPTIMAL", "ip_status": "OPTIMAL",
                           "bound": None}
    assert p["catalog_sha256"] == "c" * 64 and p["solver_run_id"] == "r-p-1"
    assert p["chains"] == 5 and p["coverage"] == {"complete": True}
    assert rec["rostering"] is None
    m = rec["models"]
    assert m["certifier"]["model"] == "claude-sonnet-5"
    assert m["certifier"]["provider"] == "anthropic"
    assert m["certifier"]["note_code"] == "channel-does-not-set"
    assert m["certifier"]["effort"] is None and m["certifier"]["temperature"] is None
    assert m["certifier"]["exchanges"] == {"present": False,
                                          "reason_code": "pre-passport"}
    assert m["certifier"]["calls_code"] == "live"
    assert m["arbiter"] == {"model": None, "reason_code": "none-in-cosmos"}
    assert m["cdpm"] is None
    assert m["source_check_reader"]["reader"] is None
    assert rec["code"]["reason_code"] == "pre-passport"
    assert rec["template_catalog"]["reason_code"] == "pre-passport"
    assert rec["registry"]["reason_code"] == "no-snapshot-copy"
    # Запись прежней версии не несёт ни решателей с параметрами, ни
    # происхождения, ни расхода моделей (10.09.2026): рецепт молчит
    # КОДОМ причины, а не выдуманными числами. Прогон-источник и
    # отпечаток пакета известны и без записи — они лежат в манифесте.
    assert rec["solvers"] is None and rec["solvers_code"] == "pre-solvers"
    assert rec["lineage"] == {"package_sha256": sha,
                              "source_run": "run-9001",
                              "parent_attempt": None, "record_id": None,
                              "reason_code": "pre-lineage"}
    assert rec["resources"] == {"models": [], "models_code": "pre-resources",
                                "solver_seconds": None,
                                "solver_seconds_code": "pre-resources"}
    assert rec["checks"]["vmp"] == {"certified": True, "certificate_gate": [],
                                    "counters": {"checked": 3}, "findings": 0,
                                    "uncovered": []}
    assert rec["checks"]["idak"] is None
    a = rec["artifacts"]
    assert a["delivery"] == "attempts/A1/button-pairing/delivery.json"
    assert a["builder_catalog"] == "attempts/A1/button-pairing/builder-catalog.json"
    assert a["certificate"] and a["builder_input"]
    assert a["solution"] is None and a["package"] is None
    assert a["registry"] is None and a["replay_report"] is None
    assert a["exchanges_index"] is None and a["call_journal"] is None


def test_recipe_uses_recorded_passport_fields_and_snapshot_copies(tmp_path):
    from webapp import cosmos_runs as cr
    sha, brief = _receive(tmp_path)
    wd = tmp_path / "data-root" / "package-runs" / sha
    rec_in = _rec(
        "R1", "rostering", run="run-0001", package_sha=sha,
        params={"mode": "optimization", "objective_id": "G-1",
                "spent_limit_s": 120, "unimproved_spent_limit_s": None,
                "pairing_run_id": "r-p-1", "moves": "chain-seed",
                "accepted_uncovered": None, "poll_s": None,
                "deterministic": {"seed": 7, "step_limit": 1000,
                                  "unimproved_step_limit": None,
                                  "deterministic_time": None}},
        reproducibility={"class": "deterministic", "basis_code": "profile-d1",
                         "workers": 1},
        certifier={"scope": "local-only", "backend": "local",
                   "channel": "local", "model": "qwen", "provider": "ollama",
                   "effort": None, "temperature": 0.2,
                   "note_code": "ollama-default"},
        code={"commit": "c" * 40, "commit_short": "c" * 7, "tree_dirty": False,
              "dirty_paths": 0},
        template_catalog={"version": "0.1.0-draft.3", "fingerprint": "b" * 64},
        solver={"url": SOLVER_URL, "engine": "timefold"},
        registry={"source": "derived", "sha256": "r" * 64,
                  "catalog": {"version": "0.1.0-draft.3"}},
        exchanges={"dir": "attempts/R1/button/llm-exchanges", "count": 2,
                   "complete": True},
        journal={"dir": "attempts/R1/button/external-calls", "complete": True,
                 "counts": {"solver": 5}, "degraded": None},
        replay={"of": {"mode": "journal", "source_attempt": "R0",
                       "report": {"verdict": "same", "words": "совпадает"}}},
        source_check={"task": "rostering", "at": "2026-09-07T11:00:00",
                      "reader": "cloud/claude-sonnet-5", "reader_channel": "cli",
                      "counts": {"violated": 1, "satisfied": 8},
                      "snapshot": "source-check/rostering/last.json"},
        pairing_selection={"pairing_run_id": "r-p-1", "attempt_id": "A1"},
        actor="user:kto@example.com",
        catalog="attempts/R1/button-on-pairing")
    _state(wd, [rec_in], attempts_trimmed=2)
    out = wd / "attempts" / "R1" / "button-on-pairing"
    _wj(out / "delivery.json", {"verdict": "допустимо", "certified": True,
                                "schedule": {"solver_run_id": "r-9",
                                             "score": "0hard/-3soft"},
                                "pairing_run_id": "r-p-1",
                                "pairing_catalog_sha256": "c" * 64,
                                "findings": 2, "uncovered": []})
    _wj(out / "solver-status.json", {"profile": "d1", "moves": "chain-seed",
                                     "seeded": True, "deadheads": 0})
    _wj(out / "applied-rules.json", {"max_duty": 690})
    _wj(out / "solution.json", {"thresholds": {"w": 1}, "assignments": []})
    _wj(wd / "attempts" / "R1" / "registry" / "vmp-executors-registry.json",
        {"created": "x", "catalog": {"version": "0.1.0-draft.3"}})
    (out / "llm-exchanges").mkdir(parents=True)
    (out / "llm-exchanges" / "index.jsonl").write_bytes(b'{"n": 1}\n{"n": 2}\n')
    (out / "replay").mkdir(parents=True, exist_ok=True)
    (wd / "attempts" / "R1" / "replay").mkdir(parents=True, exist_ok=True)
    _wj(wd / "attempts" / "R1" / "replay" / "report.json", {"verdict": "same"})
    doc = cr.read_run(brief["cosmos_run"])
    rec = cr.attempt_recipe(wd, rec_in, doc["packages"][0])
    assert rec["attempt"]["kind"] == "rostering_on_pairing"
    assert rec["attempt"]["journal"]["counts"] == {"solver": 5}
    assert rec["attempt"]["replay"]["of"]["mode"] == "journal"
    # Кто нажал (10.09.2026): рецепт несёт актора записи слово в слово.
    assert rec["attempt"]["actor"] == "user:kto@example.com"
    assert rec["params"] == rec_in["params"] and rec["params_reconstructed"] == []
    assert rec["params_code"] is None
    assert rec["reproducibility"]["class"] == "deterministic"
    r = rec["rostering"]
    assert r["engine"] == "timefold" and r["engine_code"] == "engine-only-timefold"
    assert r["solver_url"] == SOLVER_URL
    assert r["solver_run_id"] == "r-9" and r["score"] == "0hard/-3soft"
    assert r["request"]["spentLimitSeconds"] == 120
    assert r["request"]["pairingRunId"] == "r-p-1"
    assert r["request"]["deterministic"]["seed"] == 7
    assert r["seed"] == {"value": 7, "source_code": "profile-d1"}
    assert (r["profile"], r["moves"], r["seeded"], r["deadheads"]) == \
        ("d1", "chain-seed", True, 0)
    assert r["solver_status_code"] is None
    assert r["thresholds_applied"] == {"max_duty": 690} and r["weights"] == {"w": 1}
    assert r["pairing_run_id"] == "r-p-1" and r["pairing_catalog_sha256"] == "c" * 64
    m = rec["models"]
    assert m["certifier"]["model"] == "qwen" and m["certifier"]["provider"] == "ollama"
    assert m["certifier"]["calls_code"] == "replayed-from-journal"
    assert m["certifier"]["exchanges"]["count"] == 2
    assert m["source_check_reader"] == {"reader": "cloud/claude-sonnet-5",
                                        "reader_channel": "cli",
                                        "compiled_cache": None}
    assert rec["code"]["commit_short"] == "c" * 7
    assert rec["template_catalog"]["version"] == "0.1.0-draft.3"
    assert rec["registry"]["sha256"] == "r" * 64
    assert rec["checks"]["idak"]["counts"] == {"violated": 1, "satisfied": 8}
    assert rec["checks"]["vmp"]["findings"] == 2
    a = rec["artifacts"]
    assert a["solver_status"] == "attempts/R1/button-on-pairing/solver-status.json"
    assert a["applied_rules"] and a["solution"] and a["exchanges_index"]
    assert a["registry"] == "attempts/R1/registry/vmp-executors-registry.json"
    assert a["replay_report"] == "attempts/R1/replay/report.json"
    # паспорт прогона: сводка попытки, модели по ролям, журналы, обрезка
    p = cr.passport_of(doc)
    row = p["attempts"][0]
    assert row["id"] == "R1" and row["kind"] == "rostering_on_pairing"
    assert row["params_code"] is None and row["certifier"]["model"] == "qwen"
    assert row["exchanges"]["count"] == 2 and row["journal"]["complete"] is True
    assert row["checks"]["idak"]["counts"]["violated"] == 1
    assert row["replay"]["source_attempt"] == "R0"
    roles = {(m["role"], m["model"]) for m in p["models_summary"]}
    assert ("certifier", "qwen") in roles
    assert ("source_check_reader", "cloud/claude-sonnet-5") in roles
    assert p["journals"] == {"attempts_with_exchanges": 1, "model_exchanges": 2,
                             "source_check_snapshots": 0}
    assert p["attempts_trimmed"] == 2
    assert "pre-passport" not in p["gaps"]
    s = cr.summary_of(doc)
    assert s["models"]["certifier"] == "qwen (local)"
    assert s["models"]["source_check"] == "cloud/claude-sonnet-5 (cli)"
    tf_version = ((doc["passport"]["environment"]["solver"]["build"] or {})
                  .get("timefold_version"))
    assert s["solvers"] == [{"task": "rostering", "engine": "timefold",
                             "version": tf_version, "version_of": None,
                             "version_code": None if tf_version else "no-build-files"}]
    assert s["code"] == "c" * 7 and s["catalog"] == "0.1.0-draft.3"
    assert s["state_code"] == "ok"
    lst = {x["run"]: x for x in cr.list_runs()["runs"]}[doc["run"]]
    assert lst["attempts"]["last"]["replay_of"] == {"mode": "journal",
                                                    "source_attempt": "R0",
                                                    "verdict": "same"}
    assert lst["checks"]["idak"]["rostering"] == "bad"


def test_passport_never_invents_when_disk_is_empty(tmp_path):
    from webapp import cosmos_runs as cr
    sha, brief = _receive(tmp_path)
    wd = tmp_path / "data-root" / "package-runs" / sha
    _state(wd, [_rec("A1"), _rec("A2", "rostering")])   # снимков нет
    doc = cr.read_run(brief["cosmos_run"])
    for rec_in in (_rec("A1"), _rec("A2", "rostering")):
        rec = cr.attempt_recipe(wd, rec_in, doc["packages"][0])
        assert rec["params"] is None and rec["params_reconstructed"] == []
        assert rec["params_code"] == "pre-passport"
        assert rec["reproducibility"]["reason_code"] == "pre-passport"
        assert rec["code"]["reason_code"] == "pre-passport"
        assert rec["template_catalog"]["reason_code"] == "pre-passport"
        assert rec["registry"]["reason_code"] == "no-snapshot-copy"
        assert rec["registry"]["fallback_code"] in (None, "latest-press-registry")
        assert all(v is None for v in rec["artifacts"].values())
        assert rec["models"]["certifier"] == {"exchanges": {"present": False,
                                                            "reason_code": "pre-passport"},
                                              "calls_code": "live"}
        assert rec["checks"]["vmp"] == {"certified": None, "certificate_gate": None,
                                        "counters": None, "findings": None,
                                        "uncovered": None}
        assert rec["package"]["manifest_sha256"] is None
    rost = cr.attempt_recipe(wd, _rec("A2", "rostering"), doc["packages"][0])
    assert rost["rostering"]["solver_status_code"] == "pre-passport"
    assert rost["rostering"]["seed"] == {"value": 0,
                                         "source_code": "solver-default-seed-0"}
    assert rost["rostering"]["profile"] is None
    p = cr.passport_of(doc)
    assert [r["id"] for r in p["attempts"]] == ["A2", "A1"]
    assert all(r["params_code"] == "pre-passport" for r in p["attempts"])
    assert all(r["certifier"] is None for r in p["attempts"])
    assert "pre-passport" in p["gaps"] and "not-declared-by-package" in p["gaps"]
    s = cr.summary_of(doc)
    assert s["state_code"] == "pre-passport"
    assert s["models"] == {"certifier": None, "source_check": None, "cdpm": None}
    assert s["solvers"][0]["task"] == "pairing" and s["solvers"][0]["engine"] == "cpsat"
    assert s["solvers"][1]["task"] == "rostering" and s["solvers"][1]["engine"] == "timefold"


def test_source_check_binding_reaches_attempt_record_and_filters_other_package(
        tmp_path):
    from webapp import cosmos_runs as cr
    sha, brief = _receive(tmp_path)
    run = brief["cosmos_run"]
    wd = tmp_path / "data-root" / "package-runs" / sha
    _state(wd, [_rec("A1")])
    doc = cr.read_run(run)
    snap = tmp_path / "data-root" / "source-check" / "pairing" / "last.json"
    _wj(snap, {"status": "done", "run": "run-9001", "reader": "cloud/claude-sonnet-5",
               "reader_channel": "cli", "counts": {"violated": 0, "satisfied": 3},
               "package_sha": "f" * 64, "cosmos_run": run})
    rec = cr.attempt_recipe(wd, _rec("A1", run=run), doc["packages"][0])
    assert rec["models"]["source_check_reader"] == {
        "reader": None, "reader_channel": None,
        "reason_code": "snapshot-of-other-package"}
    assert cr.passport_of(doc)["journals"]["source_check_snapshots"] == 1
    _wj(snap, {"status": "done", "run": "run-9001", "reader": "cloud/claude-sonnet-5",
               "reader_channel": "cli", "counts": {"violated": 0, "satisfied": 3},
               "package_sha": sha, "cosmos_run": "run-0042"})
    rec = cr.attempt_recipe(wd, _rec("A1", run=run), doc["packages"][0])
    assert rec["models"]["source_check_reader"]["reason_code"] == \
        "snapshot-of-other-package"
    _wj(snap, {"status": "done", "run": "run-9001", "reader": "cloud/claude-sonnet-5",
               "reader_channel": "cli", "counts": {"violated": 0, "satisfied": 3},
               "package_sha": sha, "cosmos_run": run})
    rec = cr.attempt_recipe(wd, _rec("A1", run=run), doc["packages"][0])
    assert rec["models"]["source_check_reader"]["reader"] == "cloud/claude-sonnet-5"
    assert rec["models"]["source_check_reader"]["reader_channel"] == "cli"
    # снимок до версии (без cosmos_run) при совпавшем пакете — приписывается
    _wj(snap, {"status": "done", "run": "run-9001", "reader": "cli/x",
               "counts": {"violated": 2}, "package_sha": sha})
    rec = cr.attempt_recipe(wd, _rec("A1", run=run), doc["packages"][0])
    assert rec["models"]["source_check_reader"]["reader"] == "cli/x"
    lst = {x["run"]: x for x in cr.list_runs()["runs"]}[run]
    assert lst["checks"]["idak"]["pairing"] == "bad"


def test_recipe_solver_status_code_names_not_solved_not_pre_passport(tmp_path):
    """solver-status.json пишется кнопкой только после выхода из опроса
    состоянием «готов»: у попытки текущей версии (params записаны),
    остановленной раньше — «решатель недоступен», «отверг пакет», «упал»,
    превышено время ожидания — рецепт называет пробел кодом not-solved,
    а pre-passport оставляет попыткам до версии (без params)."""
    from webapp import cosmos_runs as cr
    sha, brief = _receive(tmp_path)
    wd = tmp_path / "data-root" / "package-runs" / sha
    params = {"mode": "feasibility-hard", "objective_id": None,
              "spent_limit_s": 60, "unimproved_spent_limit_s": None,
              "pairing_run_id": None, "moves": None, "accepted_uncovered": None,
              "poll_s": None, "deterministic": None}
    stopped = _rec("R-stop", "rostering", run="run-0001", package_sha=sha,
                   params=params, verdict="остановка: решатель недоступен",
                   reproducibility={"class": "wall-clock",
                                    "basis_code": "wall-clock-limit", "workers": None})
    old = _rec("R-old", "rostering", run="run-0001", package_sha=sha,
               verdict="остановка: решатель недоступен")
    solved = _rec("R-ok", "rostering", run="run-0001", package_sha=sha, params=params)
    _state(wd, [old, stopped, solved])
    for aid in ("R-stop", "R-old", "R-ok"):
        out = wd / "attempts" / aid / "button"
        _wj(out / "delivery.json", {"verdict": "x", "journal": []})
    _wj(wd / "attempts" / "R-ok" / "button" / "solver-status.json",
        {"state": "готов", "score": "0hard/0soft", "profile": "wall-clock"})
    assert cr.attempt_recipe(wd, stopped)["rostering"]["solver_status_code"] == "not-solved"
    assert cr.attempt_recipe(wd, old)["rostering"]["solver_status_code"] == "pre-passport"
    ok = cr.attempt_recipe(wd, solved)["rostering"]
    assert ok["solver_status_code"] is None and ok["profile"] == "wall-clock"


def test_summary_form_for_list(tmp_path):
    from webapp import cosmos_runs as cr
    sha, brief = _receive(tmp_path)
    wd = tmp_path / "data-root" / "package-runs" / sha
    _state(wd, [
        _rec("P1", engine="cpsat", started_at="2026-09-07T09:00:00",
             params={"spent_limit_s": 60}, engine_versions={"ortools": "9.15.6755"},
             certifier={"scope": "any", "backend": "cloud", "channel": "cli",
                        "model": "claude-sonnet-5"},
             code={"commit_short": "c59300b"},
             template_catalog={"version": "0.1.0-draft.3"}),
        _rec("R1", "rostering", started_at="2026-09-07T10:00:00",
             params={"spent_limit_s": 240},
             certifier={"scope": "any", "backend": "cloud", "channel": "api",
                        "model": "claude-sonnet-5"},
             code={"commit_short": "d1e2f3a"},
             template_catalog={"version": "0.1.0-draft.4"})])
    doc = cr.read_run(brief["cosmos_run"])
    s = cr.summary_of(doc)
    assert s["models"]["certifier"] == "claude-sonnet-5 (api)"
    # решатели — кодами и полями (слова собирает клиент на языке
    # интерфейса), не русской фразой сервера
    assert s["solvers"][0] == {"task": "pairing", "engine": "cpsat",
                               "version": "9.15.6755", "version_of": "ortools",
                               "version_code": None}
    assert s["solvers"][1]["task"] == "rostering" and s["solvers"][1]["engine"] == "timefold"
    assert set(s["solvers"][1]) == {"task", "engine", "version", "version_of", "version_code"}
    assert (s["solvers"][1]["version_code"] is None) == bool(s["solvers"][1]["version"])
    assert not any(isinstance(x, str) for x in s["solvers"])
    assert s["code"] == "d1e2f3a" and s["catalog"] == "0.1.0-draft.4"
    assert s["state_code"] == "ok"
    assert set(s) == {"models", "solvers", "code", "catalog", "state_code"}
    lst = cr.list_runs()["runs"][0]
    assert lst["passport_summary"] == s
    assert lst["attempts"] == {"total": 2, "pairing": 1, "rostering": 1,
                               "running": 0,
                               "last": {"id": "R1", "task": "rostering",
                                        "kind": "rostering", "verdict": "допустимо",
                                        "progress": None, "error": None,
                                        "finished_at": "2026-09-07T10:05:00",
                                        "started_at": "2026-09-07T10:00:00"}}
