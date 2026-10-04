# -*- coding: utf-8 -*-
"""Прогоны COSMOS (webapp/cosmos_runs.py + ручки webapp/app.py) —
стенд проекта final-report/COSMOS-PROGONY-PROEKT-CLD.md, раздел 13.1.

Охраняемое: хранилище живёт и умирает вместе с инбоксом и НИКОГДА не
пишет из читающих путей (список, карточка, /api/flow, старт приложения);
номер не выдаётся повторно; текущий прогон — один указатель, зеркало
канала пишется последним и только из прогона; канал кладёт пакет в
текущий прогон и не отказывает (автосоздание); тот же sha во втором
прогоне — подкаталог runs/<run> с маркером держателя; доступ по токену
— только к перечисленным парам (метод, путь); параметры пути
проверяются до диска; состояния прогона — коды девяти видов.

Пакеты синтетические (сборка cdpm.export_task_package на фикстурах
test_cdpm_task_package) — данных заказчика нет; каталог данных каждого
теста — в tmp."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import pytest
from _stand_identity import ADMIN_EMAIL

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from tests.test_cdpm_task_package import _ready_doc  # noqa: E402
from tests.test_task_channel import _machined_doc, _pkg_bytes  # noqa: E402

ADMIN = ADMIN_EMAIL
TOKEN = "test-channel-token-2026"
RUNS = "/api/cosmos-runs"
ISO_OFFSET = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """TestClient в dev-режиме с каталогом данных в tmp ДО старта
    lifespan (образец 3.4 проекта: не переиспользовать клиент канала)."""
    from webapp import users
    cfg = tmp_path_factory.mktemp("cruns-auth")
    data = tmp_path_factory.mktemp("cruns-data")
    mp = pytest.MonkeyPatch()
    mp.setenv("CREW_AUTH_MODE", "dev")
    mp.setenv("CREW_DEV_USER", ADMIN)
    mp.delenv("CREW_ACCESS_AUD", raising=False)
    mp.setenv("COSMOS_SKIP_CONFIG_MIGRATION", "1")
    mp.setenv("CREW_DATA_DIR", str(data))
    mp.delenv("CREW_TASK_INBOX", raising=False)
    mp.setattr(users, "USERS_DB_PATH", cfg / "users.db")
    users.ensure_db()
    import importlib
    from webapp import app as app_module
    importlib.reload(app_module)
    from fastapi.testclient import TestClient
    try:
        with TestClient(app_module.app) as c:
            c.data_root = data                          # для гейта lifespan
            yield c
    finally:
        mp.undo()


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data-root"))
    for k in ("CREW_TASK_INBOX", "CREW_TASK_PACKAGE", "CREW_VMP_REGISTRY",
              "CREW_SOLVER_URL", "CREW_COSMOS_RUNS_DIR", "CREW_CHANNEL_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    from webapp import cosmos_runs, flow, task_channel
    monkeypatch.setitem(flow._READY_CACHE, "key", None)
    monkeypatch.setitem(flow._READY_CACHE, "value", None)
    monkeypatch.setitem(task_channel._CARD_CACHE, "key", None)
    monkeypatch.setitem(task_channel._CARD_CACHE, "value", None)
    cosmos_runs.forget()


# ------------------------------------------------------------ помощники

_BYTES: dict[str, bytes] = {}


def _bytes(tmp_path: Path, which: str = "a") -> bytes:
    """Байты синтетического пакета «a» / «b» / «c» — собираются один раз
    на модуль (книги данных несут метки времени: две сборки подряд не
    байт-идентичны, а тесты повтора обязаны слать те же байты)."""
    if which not in _BYTES:
        base = tmp_path / ("pkg-" + which)
        if which == "a":
            _BYTES[which] = _pkg_bytes(base)
        elif which == "b":
            _BYTES[which] = _pkg_bytes(base, _machined_doc())
        else:
            doc = _ready_doc()
            doc.setdefault("meta", {})["revision"] = 7
            _BYTES[which] = _pkg_bytes(base, doc)
    return _BYTES[which]


def _receive(tmp_path: Path, which: str = "a", source: str = "channel"):
    """Пакет тем же путём, что канал/загрузка; (sha, краткий вердикт)."""
    from webapp import task_channel
    body = _bytes(tmp_path, which)
    sha = hashlib.sha256(body).hexdigest()
    code, brief = task_channel.receive(body, sha, source=source)
    assert code == 200, brief
    return sha, brief


def _root(tmp_path: Path) -> Path:
    return tmp_path / "data-root" / "cosmos-runs"


def _inbox(tmp_path: Path) -> Path:
    return tmp_path / "data-root" / "task-inbox"


def _rj(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _att(aid: str, task: str = "pairing", done: bool = True,
         verdict: str | None = "пейринг построен", **kw) -> dict:
    rec = {"id": aid, "task": task, "pairing_selection": None,
           "engine": "cpsat" if task == "pairing" else None,
           "started_at": kw.pop("started_at", "2026-09-07T10:00:00"),
           "state": "завершено" if done else "идёт", "done": done,
           "verdict": verdict, "error": None,
           "finished_at": "2026-09-07T10:05:00" if done else None,
           "catalog": "attempts/%s/%s" % (aid, "button-pairing"
                                          if task == "pairing" else "button")}
    rec.update(kw)
    return rec


def _write_state(wd: Path, attempts: list, **extra) -> None:
    wd.mkdir(parents=True, exist_ok=True)
    st = {"attempts": attempts, "done": True, "task": attempts[-1]["task"]
          if attempts else None}
    st.update(extra)
    (wd / "state.json").write_text(json.dumps(st, ensure_ascii=False),
                                   encoding="utf-8")


def _make_busy(monkeypatch, wd: Path, task: str = "rostering") -> None:
    """Занятая линия press с живой нитью (None в реестре = «занята,
    стартует», как между занятием и запуском нити)."""
    from webapp import package_runs as pr
    _write_state(wd, [_att("A-busy", task, done=False, verdict=None)],
                 done=False, task=task, pid=os.getpid(),
                 progress="Ш-2 — принят решателем", attempt_id="A-busy")
    monkeypatch.setitem(pr._RUNNING, pr._line_key(wd, "press"), None)


# ------------------------------------------------- хранилище и изоляция

def test_runs_root_none_when_inbox_disabled_and_nothing_written(
        tmp_path, monkeypatch):
    from webapp import cosmos_runs as cr, task_channel
    monkeypatch.setenv("CREW_TASK_INBOX", "")
    monkeypatch.delenv("CREW_DATA_DIR")
    calls = {"n": 0}
    real = os.replace

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)
    monkeypatch.setattr(os, "replace", counting)
    assert cr.runs_root() is None and cr.storage_state() == "off"
    assert cr.list_runs()["runs"] == [] and cr.list_runs()["inbox_disabled"]
    assert cr.current_run() is None and task_channel.current_package() is None
    assert cr.current_run_summary() is None and cr.hint_code() == "storage-off"
    with pytest.raises(cr.RunsError) as ei:
        cr.create_run("x")
    assert ei.value.code == "storage-off"
    assert calls["n"] == 0, "читающие пути и отказ не пишут ни байта"
    # пустой CREW_COSMOS_RUNS_DIR — тоже выключено, даже при живом инбоксе
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.delenv("CREW_TASK_INBOX")
    monkeypatch.setenv("CREW_COSMOS_RUNS_DIR", "")
    assert cr.runs_root() is None
    monkeypatch.setenv("CREW_COSMOS_RUNS_DIR", str(tmp_path / "elsewhere"))
    assert cr.runs_root() == tmp_path / "elsewhere"


def test_lifespan_does_not_create_storage(client):
    assert not (client.data_root / "cosmos-runs").exists()


def test_reading_paths_never_initialize_storage(client, tmp_path):
    from webapp import cosmos_runs as cr
    _receive(tmp_path)            # заводит хранилище (пишущий путь)
    assert cr.storage_state() == "ready"
    (_root(tmp_path) / "storage.json").unlink()
    assert cr.storage_state() == "uninitialized"
    assert client.get(RUNS).json()["storage"]["state"] == "uninitialized"
    assert client.get("/api/flow").json()["runs_hint_code"] == \
        "storage-not-initialized"
    assert client.get("/api/task-package/current").json()["run"] is None
    assert cr.storage_state() == "uninitialized", "чтение не оформляет"


# --------------------------------------------------------- создание

def test_create_run_number_counter_created_at_offset_and_current(
        client, tmp_path):
    r = client.post(RUNS, json={"title": "Демо 07.09"})
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["run"] == "run-0001" and doc["current"] is True
    assert doc["title"] == "Демо 07.09" and doc["origin"] == "manual"
    assert doc["created_by"] == "user:" + ADMIN
    assert ISO_OFFSET.match(doc["created_at"]), doc["created_at"]
    assert doc["passport"]["environment"]["product"] == "COSMOS"
    root = _root(tmp_path)
    assert _rj(root / "counter.json") == {"last": 1}
    assert _rj(root / "storage.json")["schema"] == "cosmos-runs/v1"
    assert _rj(root / "current.json")["run"] == "run-0001"
    assert _rj(root / "run-0001" / "run.json")["schema"] == "cosmos-run/v1"
    cur = client.get(RUNS + "/current").json()
    assert cur["run"]["run"] == "run-0001" and cur["package"] is None
    assert cur["hint_code"] is None
    r2 = client.post(RUNS, json={})
    assert r2.json()["run"] == "run-0002" and r2.json()["current"]
    assert client.get(RUNS).json()["current"] == "run-0002"
    assert client.post(RUNS, json={"title": "x" * 121}).status_code == 400


def test_create_without_make_current_keeps_pointer(client, tmp_path):
    assert client.post(RUNS, json={"title": "первый"}).json()["run"] == "run-0001"
    r = client.post(RUNS, json={"make_current": False})
    assert r.status_code == 200 and r.json()["current"] is False
    assert _rj(_root(tmp_path) / "current.json")["run"] == "run-0001"
    assert client.get(RUNS).json()["current"] == "run-0001"


def test_create_refuses_env_package_and_busy_current_in_words(
        client, tmp_path, monkeypatch):
    from webapp import cosmos_runs as cr
    sha, brief = _receive(tmp_path)
    run = brief["cosmos_run"]
    env_pkg = tmp_path / "env-pkg.zip"
    env_pkg.write_bytes(_bytes(tmp_path, "b"))
    monkeypatch.setenv("CREW_TASK_PACKAGE", str(env_pkg))
    r = client.post(RUNS, json={"title": "x"})
    assert r.status_code == 409 and r.json()["reason_code"] == "env-package"
    assert "CREW_TASK_PACKAGE" in r.json()["detail"]
    assert client.get(RUNS).json()["env_package"] is True
    monkeypatch.delenv("CREW_TASK_PACKAGE")
    wd = cr.workdir_of_package(cr.read_run(run)["packages"][0])
    _make_busy(monkeypatch, wd)
    r = client.post(RUNS, json={"title": "y"})
    assert r.status_code == 409 and r.json()["reason_code"] == "current-busy"
    assert "дождитесь" in r.json()["detail"]
    r = client.post(RUNS, json={"title": "y", "make_current": False})
    assert r.status_code == 200 and r.json()["run"] == "run-0002"
    assert client.get(RUNS).json()["current"] == run
    rec = next(x for x in client.get(RUNS).json()["runs"] if x["run"] == run)
    assert rec["state_kind"] == "working" and rec["busy"]["task"] == "rostering"


# ----------------------------------------------------- список и порядок

def test_list_order_positions_and_manual_order_put(client, tmp_path):
    for t in ("один", "два", "три"):
        client.post(RUNS, json={"title": t})
    lst = client.get(RUNS).json()
    assert [x["run"] for x in lst["runs"]] == ["run-0001", "run-0002", "run-0003"]
    assert [x["position"] for x in lst["runs"]] == [0, 1, 2]
    assert lst["current"] == "run-0003" and lst["storage"]["state"] == "ready"
    assert lst["storage"]["legacy"] is None and lst["inbox_disabled"] is False
    r = client.put(RUNS + "/order", json={"order": ["run-0003", "run-0001",
                                                    "run-0003"]})
    assert r.status_code == 200
    assert r.json()["order"] == ["run-0003", "run-0001", "run-0002"]
    assert client.get(RUNS + "/order").json()["order"] == \
        ["run-0003", "run-0001", "run-0002"]
    assert [x["run"] for x in client.get(RUNS).json()["runs"]] == \
        ["run-0003", "run-0001", "run-0002"]
    assert _rj(_root(tmp_path) / "order.json") == {"order": ["run-0003", "run-0001"]}


def test_order_put_rejects_unknown_and_malformed_ids_writes_nothing(
        client, tmp_path):
    client.post(RUNS, json={})
    r = client.put(RUNS + "/order", json={"order": ["run-0099"]})
    assert r.status_code == 400 and "не существует" in r.json()["detail"]
    r = client.put(RUNS + "/order", json={"order": ["x"]})
    assert r.status_code == 400 and "недопустимый номер" in r.json()["detail"]
    r = client.put(RUNS + "/order", json={"order": "run-0001"})
    assert r.status_code == 400 and "список order" in r.json()["detail"]
    assert not (_root(tmp_path) / "order.json").exists()


# ------------------------------------------------------------- архив

def test_archive_moves_cleans_order_raises_counter(client, tmp_path):
    for _ in range(3):
        client.post(RUNS, json={})
    client.put(RUNS + "/order", json={"order": ["run-0001", "run-0002"]})
    r = client.post(RUNS + "/archive", json={"runs": ["run-0001"]})
    assert r.status_code == 200, r.text
    assert r.json()["archived"] == ["run-0001"] and r.json()["left"] == 2
    arch = tmp_path / "data-root" / "cosmos-runs-archive"
    assert (arch / "run-0001" / "run.json").is_file()
    assert not (_root(tmp_path) / "run-0001").exists()
    assert _rj(_root(tmp_path) / "order.json") == {"order": ["run-0002"]}
    assert _rj(_root(tmp_path) / "counter.json")["last"] == 3
    assert [x["run"] for x in client.get(RUNS).json()["runs"]] == \
        ["run-0002", "run-0003"]
    r = client.post(RUNS + "/archive", json={"runs": []})
    assert r.status_code == 400
    r = client.post(RUNS + "/archive", json={"runs": ["run-0042"]})
    assert r.status_code == 404 and "ничего не перенесено" in r.json()["detail"]
    r = client.post(RUNS + "/archive", json={"runs": ["bad"]})
    assert r.status_code == 400


def test_archive_refuses_busy_current_unreadable_fail_closed(
        client, tmp_path, monkeypatch):
    from webapp import cosmos_runs as cr
    sha, brief = _receive(tmp_path)                       # run-0001 с пакетом
    client.post(RUNS, json={})                             # run-0002 текущий
    client.post(RUNS, json={"make_current": False})        # run-0003
    r = client.post(RUNS + "/archive", json={"runs": ["run-0002", "run-0003"]})
    assert r.status_code == 409 and r.json()["reason_code"] == "current-run"
    assert (_root(tmp_path) / "run-0003").is_dir()
    wd = cr.workdir_of_package(cr.read_run("run-0001")["packages"][0])
    _make_busy(monkeypatch, wd)
    r = client.post(RUNS + "/archive", json={"runs": ["run-0001", "run-0003"]})
    assert r.status_code == 409 and r.json()["reason_code"] == "run-busy"
    assert "run-0001" in r.json()["detail"] and "rostering" in r.json()["detail"]
    assert (_root(tmp_path) / "run-0003").is_dir()
    (_root(tmp_path) / "run-0003" / "run.json").write_text("{кривой", encoding="utf-8")
    r = client.post(RUNS + "/archive", json={"runs": ["run-0003"]})
    assert r.status_code == 409 and r.json()["reason_code"] == "run-unreadable"
    assert (_root(tmp_path) / "run-0003").is_dir()
    lst = client.get(RUNS).json()
    bad = next(x for x in lst["runs"] if x["run"] == "run-0003")
    assert bad["state_kind"] is None and bad["error_code"] == "run-unreadable"


def test_archive_partial_failure_keeps_files_consistent(client, tmp_path,
                                                        monkeypatch):
    from webapp import cosmos_runs as cr
    for _ in range(3):
        client.post(RUNS, json={})
    client.put(RUNS + "/order", json={"order": ["run-0002", "run-0001"]})
    real = os.rename

    def failing(src, dst):
        if Path(src).name == "run-0002":
            raise OSError("занято")
        return real(src, dst)
    monkeypatch.setattr(cr.os, "rename", failing)
    r = client.post(RUNS + "/archive", json={"runs": ["run-0001", "run-0002"]})
    assert r.status_code == 500 and "run-0002" in r.json()["detail"]
    assert "уже в архиве: run-0001" in r.json()["detail"]
    assert (_root(tmp_path) / "run-0002").is_dir()
    assert not (_root(tmp_path) / "run-0001").exists()
    assert _rj(_root(tmp_path) / "order.json") == {"order": ["run-0002"]}


def test_archive_twice_same_number_keeps_both_copies(client, tmp_path):
    from webapp import cosmos_runs as cr
    client.post(RUNS, json={})
    client.post(RUNS, json={})
    assert client.post(RUNS + "/archive", json={"runs": ["run-0001"]}).status_code == 200
    # папку вернули из архива руками — принадлежность восстановлена
    import shutil
    shutil.copytree(tmp_path / "data-root" / "cosmos-runs-archive" / "run-0001",
                    _root(tmp_path) / "run-0001")
    assert cr.read_run("run-0001")["run"] == "run-0001"
    assert client.post(RUNS + "/archive", json={"runs": ["run-0001"]}).status_code == 200
    names = sorted(p.name for p in
                   (tmp_path / "data-root" / "cosmos-runs-archive").iterdir())
    assert names[0] == "run-0001" and len(names) == 2
    assert re.fullmatch(r"run-0001-\d{8}-\d{6}", names[1]), names


def test_number_after_archiving_newest_is_not_reused(client, tmp_path):
    client.post(RUNS, json={})
    client.post(RUNS, json={})
    client.put(RUNS + "/current", json={"run": "run-0001"})
    assert client.post(RUNS + "/archive", json={"runs": ["run-0002"]}).status_code == 200
    assert client.post(RUNS, json={}).json()["run"] == "run-0003"


# ---------------------------------------------------- название, текущий

def test_title_put_limits_and_clears(client, tmp_path):
    client.post(RUNS, json={})
    r = client.put(RUNS + "/run-0001/title", json={"title": "  Демо  "})
    assert r.status_code == 200 and r.json() == {"run": "run-0001", "title": "Демо"}
    assert client.get(RUNS + "/run-0001").json()["title"] == "Демо"
    assert client.put(RUNS + "/run-0001/title", json={"title": "x" * 121}).status_code == 400
    r = client.put(RUNS + "/run-0001/title", json={"title": ""})
    assert r.json()["title"] is None
    assert client.get(RUNS + "/run-0001").json()["title"] is None


def test_put_current_and_title_on_archived_run_are_404(client, tmp_path):
    client.post(RUNS, json={})
    client.post(RUNS, json={})
    assert client.post(RUNS + "/archive", json={"runs": ["run-0001"]}).status_code == 200
    assert client.put(RUNS + "/current", json={"run": "run-0001"}).status_code == 404
    assert client.put(RUNS + "/run-0001/title", json={"title": "x"}).status_code == 404
    assert client.get(RUNS + "/run-0001").status_code == 404
    r = client.put(RUNS + "/current", json={"run": "bad"})
    assert r.status_code == 400 and "недопустимый номер" in r.json()["detail"]


def test_put_current_writes_pointer_and_mirror_and_deletes_mirror_for_empty_run(
        client, tmp_path):
    from webapp import cosmos_runs as cr, task_channel
    sha, brief = _receive(tmp_path)
    mirror = _inbox(tmp_path) / "current.json"
    assert _rj(mirror)["sha"] == sha
    assert client.post(RUNS, json={"make_current": False}).json()["run"] == "run-0002"
    r = client.put(RUNS + "/current", json={"run": "run-0002"})
    assert r.status_code == 200 and r.json()["run"]["run"] == "run-0002"
    assert r.json()["package"] is None
    assert _rj(_root(tmp_path) / "current.json")["run"] == "run-0002"
    assert not mirror.exists(), "у прогона без пакета зеркала нет"
    assert task_channel.current_package() is None
    assert client.get("/api/task-package/current").json() == {"present": False}
    r = client.put(RUNS + "/current", json={"run": "run-0001"})
    assert r.status_code == 200 and r.json()["package"]["sha"] == sha
    assert _rj(mirror)["sha"] == sha and _rj(mirror)["source"] == "channel"
    cur = task_channel.current_package()
    assert cur["sha"] == sha and cur["run"] == {
        "run": "run-0001", "title": None,
        "workdir": "package-runs/" + sha, "implicit": False}
    assert cur["path"] == str(_inbox(tmp_path) / sha / "package.zip")
    assert cr.current_state()["hint_code"] is None


def test_put_current_refuses_busy_in_words(client, tmp_path, monkeypatch):
    from webapp import cosmos_runs as cr
    sha, brief = _receive(tmp_path)
    client.post(RUNS, json={"make_current": False})
    wd = cr.workdir_of_package(cr.read_run("run-0001")["packages"][0])
    _make_busy(monkeypatch, wd)
    r = client.put(RUNS + "/current", json={"run": "run-0002"})
    assert r.status_code == 409 and r.json()["reason_code"] == "current-busy"
    assert _rj(_root(tmp_path) / "current.json")["run"] == "run-0001"
    # тот же прогон — не смена, отказа нет
    assert client.put(RUNS + "/current", json={"run": "run-0001"}).status_code == 200


# ------------------------------------------------------ канал и загрузка

def test_receive_lands_in_current_run_and_brief_names_it(client, tmp_path):
    from webapp import cosmos_runs as cr
    client.post(RUNS, json={"title": "Демо"})
    sha, brief = _receive(tmp_path)
    assert brief["cosmos_run"] == "run-0001" and brief["verdict"] == "OK"
    doc = cr.read_run("run-0001")
    assert doc["current_package"] == sha and len(doc["packages"]) == 1
    pkg = doc["packages"][0]
    assert pkg["sha"] == sha and pkg["source"] == "channel"
    assert pkg["workdir"] == "package-runs/" + sha and pkg["receipts"] == 1
    assert pkg["path"] == str(_inbox(tmp_path) / sha / "package.zip")
    assert pkg["source_run"] == "run-9001" and pkg["spec_version"]
    assert pkg["verdict"] == "OK" and pkg["mg"]["pairing"]["state"]
    assert pkg["manifest_sha256"] and len(pkg["manifest_sha256"]) == 64
    assert pkg["files_count"] >= 3 and pkg["note_code"] is None
    assert pkg["made_by"] is None and pkg["kit"] is None
    assert isinstance(pkg["data_fingerprint"], dict)
    holder = _rj(tmp_path / "data-root" / "package-runs" / sha / "holder.json")
    assert holder["run"] == "run-0001" and holder["kind"] == "run"
    # вердикт — свойство пакета: прогона в нём нет
    assert "cosmos_run" not in _rj(_inbox(tmp_path) / sha / "verdict.json")
    rows = [json.loads(x) for x in (_inbox(tmp_path) / "receipts.jsonl")
            .read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["cosmos_run"] == "run-0001"
    lst = client.get(RUNS).json()
    rec = lst["runs"][0]
    assert rec["package"]["sha12"] == sha[:12] and rec["package"]["source"] == "channel"
    assert rec["package"]["launch"]["as_of"] == "live"
    assert rec["state_kind"] in ("accepted", "blocked")
    card = client.get(RUNS + "/run-0001").json()
    assert card["packages"][0]["current"] and "live_mg" in card["packages"][0]
    assert card["passport"]["inputs"][0]["sha256"] == sha
    assert card["passport"]["inputs"][0]["catalog_code"] == "not-declared-by-package"


def test_receive_without_current_run_creates_one_auto(client, tmp_path):
    from webapp import cosmos_runs as cr
    sha, brief = _receive(tmp_path)
    assert brief["cosmos_run"] == "run-0001"
    doc = cr.read_run("run-0001")
    assert doc["origin"] == "auto-receive" and doc["created_by"] == "channel"
    assert cr.current_run_id() == "run-0001"
    client.post(RUNS + "/archive", json={"runs": []})     # ничего
    # указатель ведёт на архивированный прогон → следующий приём создаёт новый
    client.post(RUNS, json={"make_current": False})
    client.put(RUNS + "/current", json={"run": "run-0002"})
    client.put(RUNS + "/current", json={"run": "run-0001"})
    import shutil
    shutil.move(str(_root(tmp_path) / "run-0001"),
                str(tmp_path / "data-root" / "cosmos-runs-archive" / "run-0001"))
    sha_b, brief_b = _receive(tmp_path, "b", source="upload")
    assert brief_b["cosmos_run"] == "run-0003"
    assert cr.read_run("run-0003")["origin"] == "auto-upload"


def test_upload_source_kind_is_upload_in_card(client, tmp_path):
    from webapp import task_channel
    sha, brief = _receive(tmp_path, "a", source="upload")
    card = task_channel.card_state()
    assert card["present"] and card["source"]["kind"] == "upload"
    assert card["run"] == {"run": "run-0001", "title": None, "implicit": False}
    api = client.get("/api/task-package/current").json()
    assert api["source"]["kind"] == "upload" and api["run"]["run"] == "run-0001"
    assert _rj(_inbox(tmp_path) / "current.json")["source"] == "upload"


def test_repeat_same_sha_increments_receipts_and_keeps_workdir(client, tmp_path):
    from webapp import cosmos_runs as cr
    sha, brief = _receive(tmp_path)
    sha_b, _ = _receive(tmp_path, "b")
    sha2, brief2 = _receive(tmp_path)
    assert brief2["already_accepted"] and brief2["cosmos_run"] == "run-0001"
    doc = cr.read_run("run-0001")
    assert [p["sha"] for p in doc["packages"]] == [sha, sha_b]
    assert doc["packages"][0]["receipts"] == 2
    assert doc["packages"][0]["last_received_at"] >= doc["packages"][0]["received_at"]
    assert doc["packages"][0]["workdir"] == "package-runs/" + sha
    assert doc["current_package"] == sha
    mirror = _rj(_inbox(tmp_path) / "current.json")
    assert mirror["sha"] == sha and mirror["repeat"] is True


def test_new_package_keeps_previous_package_and_attempts(client, tmp_path):
    from webapp import cosmos_runs as cr
    sha, _ = _receive(tmp_path)
    wd = tmp_path / "data-root" / "package-runs" / sha
    _write_state(wd, [_att("A1")])
    (wd / "attempts" / "A1" / "button-pairing").mkdir(parents=True)
    (wd / "attempts" / "A1" / "button-pairing" / "delivery.json").write_text(
        json.dumps({"verdict": "пейринг построен", "certified": True}),
        encoding="utf-8")
    sha_b, brief_b = _receive(tmp_path, "b")
    doc = cr.read_run("run-0001")
    assert doc["current_package"] == sha_b and len(doc["packages"]) == 2
    card = client.get(RUNS + "/run-0001").json()
    assert [a["id"] for a in card["attempts"]] == ["A1"]
    att = card["attempts"][0]
    assert att["package_sha"] == sha and att["run"] == "run-0001"
    assert att["kind"] == "pairing"
    assert {"alias": "выдача-кнопки-пейринга",
            "href": RUNS + "/run-0001/attempts/A1/file/выдача-кнопки-пейринга"} \
        in att["files"]
    assert att["journal"] == {"present": False, "reason_code": "pre-journal"}
    assert att["replay"]["journal"]["available"] is False
    lst = client.get(RUNS).json()["runs"][0]
    assert lst["packages_count"] == 2 and lst["attempts"]["total"] == 1
    assert lst["attempts"]["last"]["id"] == "A1"
    assert lst["state_kind"] == "done"


def test_same_sha_in_second_run_gets_runs_subdir_and_holder(client, tmp_path):
    from webapp import cosmos_runs as cr, task_channel
    sha, _ = _receive(tmp_path)
    client.post(RUNS, json={"title": "второй"})
    sha2, brief2 = _receive(tmp_path)
    assert brief2["already_accepted"] and brief2["cosmos_run"] == "run-0002"
    sub = "package-runs/%s/runs/run-0002" % sha
    assert cr.read_run("run-0002")["packages"][0]["workdir"] == sub
    assert cr.read_run("run-0001")["packages"][0]["workdir"] == "package-runs/" + sha
    assert _rj(tmp_path / "data-root" / sub / "holder.json")["run"] == "run-0002"
    assert _rj(tmp_path / "data-root" / "package-runs" / sha / "holder.json")["run"] == "run-0001"
    cur = task_channel.current_package()
    assert cur["run"]["run"] == "run-0002" and cur["run"]["workdir"] == sub
    assert cr.workdir_of(cur, sha) == tmp_path / "data-root" / sub
    # попытки раздельны: снимок во втором каталоге виден только второму
    _write_state(tmp_path / "data-root" / sub, [_att("B1")])
    assert [a["id"] for a in client.get(RUNS + "/run-0002").json()["attempts"]] == ["B1"]
    assert client.get(RUNS + "/run-0001").json()["attempts"] == []
    # /api/flow называет прогон на плашке ВМП
    st = client.get("/api/flow").json()
    assert st["run"]["run"] == "run-0002" and st["runs_hint_code"] is None
    vmp = next(b for b in st["blocks"] if b["id"] == "vmp")
    assert list(vmp["params"])[:2] == ["run", "source_run"]
    assert vmp["params"]["run"] == "run-0002"


def test_file_package_pseudo_holder_then_channel_goes_to_subdir(client, tmp_path):
    from webapp import cosmos_runs as cr
    client.post(RUNS, json={})
    body = _bytes(tmp_path)
    sha = hashlib.sha256(body).hexdigest()
    base = tmp_path / "data-root" / "package-runs" / sha
    assert cr.claim_holder(base, sha, None, "file")
    assert not cr.claim_holder(base, sha, "run-0001", "run"), "захват атомарен"
    _sha, brief = _receive(tmp_path)
    assert cr.read_run("run-0001")["packages"][0]["workdir"] == \
        "package-runs/%s/runs/run-0001" % sha
    assert _rj(base / "holder.json")["kind"] == "file"


def test_env_package_implicit_run_when_holder_names_run(client, tmp_path,
                                                        monkeypatch):
    from webapp import cosmos_runs as cr, task_channel
    sha, _ = _receive(tmp_path)
    env_pkg = tmp_path / "env-pkg.zip"
    env_pkg.write_bytes(_bytes(tmp_path))
    monkeypatch.setenv("CREW_TASK_PACKAGE", str(env_pkg))
    cur = task_channel.current_package()
    assert cur["sha"] == sha and cur["source"]["kind"] == "file"
    assert cur["run"] == {"run": "run-0001", "title": None,
                          "workdir": "package-runs/" + sha, "implicit": True}
    card = client.get("/api/task-package/current").json()
    assert card["source"]["kind"] == "file" and card["run"]["implicit"] is True
    st = client.get("/api/flow").json()
    assert st["runs_hint_code"] == "env-package" and st["run"]["implicit"]
    assert client.get(RUNS + "/current").json()["hint_code"] == "env-package"
    # пакет без держателя-прогона — неявного прогона нет
    env_pkg.write_bytes(_bytes(tmp_path, "b"))
    assert task_channel.current_package()["run"] is None
    assert cr.implicit_run_of("") is None


def test_attach_failure_named_receipt_has_null_run_mirror_intact_resend_heals(
        client, tmp_path, monkeypatch):
    from webapp import cosmos_runs as cr
    real = cr._mutate_run
    state = {"fail": True}

    def flaky(run, fn):
        if state["fail"]:
            state["fail"] = False
            raise OSError("диск занят")
        return real(run, fn)
    monkeypatch.setattr(cr, "_mutate_run", flaky)
    body = _bytes(tmp_path)
    sha = hashlib.sha256(body).hexdigest()
    from webapp import task_channel
    code, brief = task_channel.receive(body, sha)
    assert code == 200 and brief["verdict"] == "OK"
    assert brief["cosmos_run"] is None and brief["run_note_code"] == "run-not-written"
    assert "прогон не записан" in brief["detail"] and "диск занят" in brief["detail"]
    assert _rj(_inbox(tmp_path) / "current.json")["sha"] == sha, "зеркало на месте"
    rows = [json.loads(x) for x in (_inbox(tmp_path) / "receipts.jsonl")
            .read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["cosmos_run"] is None and "диск занят" in rows[-1]["run_note"]
    assert cr.read_run("run-0001")["packages"] == []
    code2, brief2 = task_channel.receive(body, sha)
    assert code2 == 200 and brief2["already_accepted"]
    assert brief2["cosmos_run"] == "run-0001"
    assert cr.read_run("run-0001")["packages"][0]["sha"] == sha


def test_repeat_without_package_zip_names_missing_bytes(client, tmp_path):
    from webapp import cosmos_runs as cr, task_channel
    body = _bytes(tmp_path)
    sha, _ = _receive(tmp_path)
    before = (_root(tmp_path) / "run-0001" / "run.json").read_bytes()
    (_inbox(tmp_path) / sha / "package.zip").unlink()
    code, brief = task_channel.receive(body, sha)
    assert code == 200 and brief["already_accepted"]
    assert brief["cosmos_run"] is None
    assert brief["run_note_code"] == "package-bytes-missing"
    assert (_root(tmp_path) / "run-0001" / "run.json").read_bytes() == before
    lst = client.get(RUNS).json()["runs"][0]
    assert lst["state_kind"] == "lost" and lst["package"]["lost"] is True
    card = client.get("/api/task-package/current").json()
    assert card == {"present": False, "note_code": "package-bytes-missing",
                    "run": {"run": "run-0001", "title": None, "implicit": False}}


# ------------------------------------------------------------ параллельность

def test_concurrent_receives_without_current_run_make_one_run(client, tmp_path):
    from webapp import cosmos_runs as cr
    shas = []
    for which in ("a", "b"):
        body = _bytes(tmp_path, which)
        sha = hashlib.sha256(body).hexdigest()
        d = _inbox(tmp_path) / sha
        d.mkdir(parents=True)
        (d / "package.zip").write_bytes(body)
        shas.append(sha)
    results = []

    def go(sha):
        results.append(cr.attach_package(sha, _inbox(tmp_path) / sha / "package.zip",
                                         source="channel", brief={"verdict": "OK"}))
    ts = [threading.Thread(target=go, args=(s,)) for s in shas]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert all(r["run"] == "run-0001" for r in results), results
    assert sorted(p["sha"] for p in cr.read_run("run-0001")["packages"]) == sorted(shas)
    assert cr._existing_run_ids(_root(tmp_path)) == ["run-0001"]


def test_concurrent_same_sha_two_runs_one_base_one_subdir(client, tmp_path):
    from webapp import cosmos_runs as cr
    client.post(RUNS, json={})
    client.post(RUNS, json={})
    sha = "ab" * 32
    got = {}

    def go(run):
        with cr.storage_lock() as root:
            got[run] = cr._choose_workdir(root, run, sha)
    ts = [threading.Thread(target=go, args=(r,)) for r in ("run-0001", "run-0002")]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert sorted(got.values()) == sorted(["package-runs/" + sha,
                                          "package-runs/%s/runs/%s" % (sha, "run-0001")]) \
        or sorted(got.values()) == sorted(["package-runs/" + sha,
                                          "package-runs/%s/runs/%s" % (sha, "run-0002")])
    base_holder = _rj(tmp_path / "data-root" / "package-runs" / sha / "holder.json")
    assert got[base_holder["run"]] == "package-runs/" + sha


def test_title_and_attach_do_not_lose_keys(client, tmp_path):
    from webapp import cosmos_runs as cr
    client.post(RUNS, json={})
    body = _bytes(tmp_path)
    sha = hashlib.sha256(body).hexdigest()
    d = _inbox(tmp_path) / sha
    d.mkdir(parents=True)
    (d / "package.zip").write_bytes(body)
    errors = []

    def titles():
        try:
            for i in range(20):
                cr.set_title("run-0001", "имя %d" % i)
        except Exception as e:                            # noqa: BLE001
            errors.append(e)

    def attach():
        try:
            for _ in range(5):
                cr.attach_package(sha, d / "package.zip", source="channel",
                                  brief={"verdict": "OK"})
        except Exception as e:                            # noqa: BLE001
            errors.append(e)
    ts = [threading.Thread(target=titles), threading.Thread(target=attach)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    assert not errors
    doc = cr.read_run("run-0001")
    assert doc["title"] == "имя 19" and doc["packages"][0]["receipts"] == 5
    assert doc["current_package"] == sha and doc["schema"] == "cosmos-run/v1"


# ------------------------------------------------------------ гейты кода

def test_run_json_written_only_through_helper():
    src = (HERE / "webapp" / "cosmos_runs.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    where_name: list[str] = []
    where_replace: list[str] = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for node in ast.walk(fn):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and "run.json" in node.value:
                where_name.append(fn.name)
            if isinstance(node, ast.Attribute) and node.attr == "replace" \
                    and isinstance(node.value, ast.Name) and node.value.id == "os":
                where_replace.append(fn.name)
    assert set(where_name) == {"_run_path"}, where_name
    assert set(where_replace) == {"_write_json_atomic"}, where_replace
    # запись файла прогона — только через _mutate_run/_write_json_atomic
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for node in ast.walk(fn):
            if isinstance(node, ast.Attribute) and node.attr in ("write_text", "write_bytes"):
                assert fn.name in ("_write_json_atomic",), fn.name


def test_resolve_current_does_not_rehash_package(client, tmp_path, monkeypatch):
    from webapp import package_runs as pr, task_channel
    sha, _ = _receive(tmp_path)

    def boom(_p):
        raise AssertionError("файл пакета хешируется при опросе")
    monkeypatch.setattr(pr, "_sha256", boom)
    cur = task_channel.current_package()
    assert cur["sha"] == sha
    st = pr.status()
    assert st["present"] and st["package_sha"] == sha
    assert client.get("/api/flow").status_code == 200


def test_read_json_retry_during_replace(tmp_path, monkeypatch):
    from webapp import cosmos_runs as cr
    p = tmp_path / "doc.json"
    p.write_text('﻿{"a": 1}', encoding="utf-8")
    real = Path.read_text
    left = {"n": 2}

    def flaky(self, *a, **k):
        if self == p and left["n"] > 0:
            left["n"] -= 1
            raise PermissionError("занят")
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", flaky)
    assert cr._read_json_retry(p) == {"a": 1} and left["n"] == 0


def test_path_params_validated_before_disk(client, tmp_path, monkeypatch):
    from webapp import cosmos_runs as cr
    client.post(RUNS, json={})

    def boom(*a, **k):
        raise AssertionError("диск тронут до проверки параметров")
    monkeypatch.setattr(cr, "read_run", boom)
    monkeypatch.setattr(cr, "resolve_attempt", boom)
    assert client.get(RUNS + "/run-00x").status_code == 404
    assert client.get(RUNS + "/RUN-0001").status_code == 404
    assert client.get(RUNS + "/run-0001x").status_code == 404
    assert client.get(RUNS + "/run-0001/attempts/a%20b").status_code == 400
    assert client.get(RUNS + "/run-0001/attempts/a%20b/file/выдача-кнопки").status_code == 400
    assert client.get(RUNS + "/run-0001/attempts/a.b/exchanges").status_code == 400
    assert client.put(RUNS + "/run-9x/title", json={"title": "a"}).status_code == 404
    assert client.get(RUNS + "/run-0001/attempts/a%20b/replay-report").status_code == 400
    r = client.post(RUNS + "/run-0001/attempts/A1/replay", json={"mode": "x"})
    assert r.status_code == 400 and "journal, fresh" in r.json()["detail"]
    # отказы параметров пути и режима — той же формы {detail, reason_code},
    # что и все отказы прогонов (контракт), кодами bad-run-id /
    # bad-attempt-id / bad-mode
    assert r.json()["reason_code"] == "bad-mode"
    r = client.get(RUNS + "/run-00x")
    assert r.json() == {"detail": "прогон 'run-00x' не существует",
                        "reason_code": "bad-run-id"}
    r = client.get(RUNS + "/run-0001/attempts/a%20b")
    assert r.json() == {"detail": "идентификатор попытки не похож на свой",
                        "reason_code": "bad-attempt-id"}
    assert client.put(RUNS + "/run-9x/title", json={"title": "a"}).json()["reason_code"] \
        == "bad-run-id"


# ---------------------------------------------------------- доступ

def test_machine_token_reads_list_creates_run_but_session_only_routes_401(
        client, tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    ok, bad = {"X-Channel-Token": TOKEN}, {"X-Channel-Token": "wrong"}
    monkeypatch.delenv("CREW_AUTH_MODE", raising=False)
    monkeypatch.delenv("CREW_ACCESS_AUD", raising=False)
    from webapp import auth
    assert auth.get_auth_mode() == "token"
    assert client.get(RUNS).status_code == 401
    assert client.get(RUNS, headers=bad).status_code == 403
    r = client.get(RUNS, headers=ok)
    assert r.status_code == 200 and r.json()["runs"] == []
    r = client.post(RUNS, headers=ok, json={"title": "машина"})
    assert r.status_code == 200 and r.json()["created_by"] == "channel"
    assert client.get(RUNS + "/current", headers=ok).json()["run"]["run"] == "run-0001"
    assert client.get(RUNS + "/run-0001", headers=ok).status_code == 200
    assert client.get(RUNS + "/run-0001/attempts/A1", headers=ok).status_code == 404
    assert client.get(RUNS + "/run-0001/attempts/A1/replay-report",
                      headers=ok).status_code == 404
    # сессионные ручки с токеном без сессии — 401 от промежуточного ПО
    for method, path, body in (("put", "/current", {"run": "run-0001"}),
                               ("put", "/order", {"order": []}),
                               ("get", "/order", None),
                               ("post", "/archive", {"runs": ["run-0001"]}),
                               ("put", "/run-0001/title", {"title": "x"}),
                               ("post", "/init", None),
                               ("post", "/run-0001/attempts/A1/replay",
                                {"mode": "journal"})):
        kw = {"headers": ok}
        if body is not None:
            kw["json"] = body
        assert getattr(client, method)(RUNS + path, **kw).status_code == 401, path


# ---------------------------------------------------------- состояния

def test_state_of_all_kinds_and_buckets(client, tmp_path, monkeypatch):
    from webapp import cosmos_runs as cr, package_runs as pr
    sha, _ = _receive(tmp_path)
    wd = tmp_path / "data-root" / "package-runs" / sha
    doc = cr.read_run("run-0001")
    empty = dict(doc, packages=[], current_package=None)
    assert cr.state_of(empty) == "new"
    kinds = {}
    kinds["accepted"] = cr.state_of(doc, attempts=[], launch={"pairing": True,
                                                              "rostering": False})
    kinds["blocked"] = cr.state_of(doc, attempts=[], launch={"pairing": False,
                                                             "rostering": False})
    _write_state(wd, [_att("A1", verdict="пейринг построен")])
    kinds["done"] = cr.state_of(cr.read_run("run-0001"))
    _write_state(wd, [_att("A2", "rostering", verdict="остановка: превышено время")])
    kinds["stalled"] = cr.state_of(doc)
    _write_state(wd, [_att("A3", "rostering", verdict=None, error="сбой")])
    kinds["refused"] = cr.state_of(doc)
    _write_state(wd, [_att("A4", "rostering", done=True, verdict=None,
                           state="прервано перезапуском")])
    kinds["interrupted"] = cr.state_of(doc)
    _write_state(wd, [_att("A5", "rostering", done=False, verdict=None)],
                 done=False, attempt_id="A5", pid=os.getpid())
    kinds["interrupted-dead"] = cr.state_of(doc)
    monkeypatch.setitem(pr._RUNNING, pr._line_key(wd, "press"), None)
    kinds["working"] = cr.state_of(doc)
    (_inbox(tmp_path) / sha / "package.zip").unlink()
    kinds["lost"] = cr.state_of(doc)
    assert kinds == {"accepted": "accepted", "blocked": "blocked",
                     "done": "done", "stalled": "stalled", "refused": "refused",
                     "interrupted": "interrupted",
                     "interrupted-dead": "interrupted",
                     "working": "working", "lost": "lost"}
    assert set(cr.STATE_BUCKETS) == set(cr.STATE_KINDS)
    assert {cr.STATE_BUCKETS[k] for k in ("new", "accepted", "blocked")} == {"new"}
    assert cr.STATE_BUCKETS["working"] == "work"
    assert {cr.STATE_BUCKETS[k] for k in ("done", "stalled")} == {"done"}
    assert {cr.STATE_BUCKETS[k] for k in ("refused", "interrupted", "lost")} == {"fail"}
    assert all(cr.STATE_WORDS[k] for k in cr.STATE_KINDS)


def test_live_launch_for_current_run_snapshot_for_others(client, tmp_path):
    from webapp import cosmos_runs as cr
    sha, _ = _receive(tmp_path)
    client.post(RUNS, json={})                              # run-0002 текущий
    lst = {x["run"]: x for x in client.get(RUNS).json()["runs"]}
    assert lst["run-0001"]["package"]["launch"]["as_of"] == "received"
    assert lst["run-0002"]["package"] is None and lst["run-0002"]["state_kind"] == "new"
    client.put(RUNS + "/current", json={"run": "run-0001"})
    lst = {x["run"]: x for x in client.get(RUNS).json()["runs"]}
    assert lst["run-0001"]["package"]["launch"]["as_of"] == "live"
    assert lst["run-0001"]["current"] is True
    assert isinstance(cr.run_card("run-0001")["packages"][0].get("live_mg"), dict)


def test_card_light_and_attempt_recipe_on_demand(client, tmp_path, monkeypatch):
    from webapp import cosmos_runs as cr
    sha, _ = _receive(tmp_path)
    wd = tmp_path / "data-root" / "package-runs" / sha
    _write_state(wd, [_att("A1")])
    out = wd / "attempts" / "A1" / "button-pairing"
    out.mkdir(parents=True)
    (out / "delivery.json").write_text(json.dumps(
        {"verdict": "пейринг построен", "mode": "feasibility-hard",
         "schedule": {"solver_run_id": "r-1"}, "chains": 3,
         "catalog_sha256": "c" * 64, "trace": {"package_fingerprint": "m" * 64}}),
        encoding="utf-8")
    (out / "builder-catalog.json").write_text(json.dumps(
        {"selected": [], "ip_status": "OPTIMAL", "solver_status": "OPTIMAL"}),
        encoding="utf-8")
    (out / "builder-input.json").write_text(json.dumps(
        {"args": ["--budget-s", "240", "--engine", "cpsat"]}), encoding="utf-8")
    heavy = []
    real = cr._read_json_opt

    def spy(path):
        if Path(path).name in ("builder-catalog.json", "solution.json"):
            heavy.append(Path(path).name)
        return real(path)
    monkeypatch.setattr(cr, "_read_json_opt", spy)
    card = client.get(RUNS + "/run-0001").json()
    assert card["attempts"][0]["id"] == "A1" and heavy == [], heavy
    r = client.get(RUNS + "/run-0001/attempts/A1")
    assert r.status_code == 200, r.text
    rec = r.json()["recipe"]
    assert rec["attempt"]["kind"] == "pairing" and rec["pairing"]["chains"] == 3
    assert rec["params"] == {"spent_limit_s": 240, "mode": "feasibility-hard"}
    assert rec["params_reconstructed"] == ["spent_limit_s", "mode"]
    assert rec["params_code"] == "pre-passport"
    assert rec["pairing"]["status"]["ip_status"] == "OPTIMAL"
    assert rec["package"]["manifest_sha256"] == "m" * 64
    assert rec["artifacts"]["delivery"] == "attempts/A1/button-pairing/delivery.json"
    assert rec["artifacts"]["solution"] is None
    assert "builder-catalog.json" in heavy
    assert "выдача-кнопки-пейринга" in r.json()["aliases"]
    r = client.get(RUNS + "/run-0001/attempts/A1/file/выдача-кнопки-пейринга")
    assert r.status_code == 200 and r.json()["chains"] == 3
    r = client.get(RUNS + "/run-0001/attempts/A1/file/пакет-кнопки-пейринга")
    assert r.status_code == 404 and r.json()["reason_code"] == "artifact-missing"
    r = client.get(RUNS + "/run-0001/attempts/A1/file/чужой")
    assert r.status_code == 404 and "перечень" in r.json()["detail"]
    r = client.get(RUNS + "/run-0001/attempts/A1/exchanges")
    assert r.status_code == 404 and r.json()["reason_code"] == "pre-passport"
    r = client.get(RUNS + "/run-0001/attempts/A1/replay-report")
    assert r.status_code == 404 and r.json()["reason_code"] == "not-a-replay"
    assert client.get(RUNS + "/run-0001/attempts/A9").status_code == 404
    # рецепт — и по ручке текущего пакета с ?attempt
    r = client.get("/api/task-package/current/press?attempt=A1")
    assert r.status_code == 200 and r.json()["recipe"]["attempt"]["id"] == "A1"


def test_card_with_unreadable_run_json_is_honest(client, tmp_path):
    client.post(RUNS, json={})
    (_root(tmp_path) / "run-0001" / "run.json").write_text("{", encoding="utf-8")
    r = client.get(RUNS + "/run-0001")
    assert r.status_code == 409 and r.json()["reason_code"] == "run-unreadable"
    lst = client.get(RUNS).json()
    assert lst["runs"][0]["error_code"] == "run-unreadable"
    assert client.get("/api/flow").status_code == 200


def test_unreadable_current_run_is_named_by_flow_current_and_package_card(
        client, tmp_path):
    """Нечитаемая запись ТЕКУЩЕГО прогона не гасит конвейер молча: схема
    несёт runs_hint_code run-unreadable и прогон с кодом, GET current —
    прогон с error_code и словами, карточка пакета — note_code и номер
    прогона (пакет на диске цел); квитанция повтора — тот же код."""
    from webapp import cosmos_runs as cr, task_channel
    sha, _ = _receive(tmp_path)
    (_root(tmp_path) / "run-0001" / "run.json").write_text("{", encoding="utf-8")
    assert cr.hint_code() == "run-unreadable"
    run, doc, err = cr.current_run_state()
    assert run == "run-0001" and doc is None and "не читается" in err
    assert cr.current_run() is None and cr.current_run_id() == "run-0001"
    flow = client.get("/api/flow").json()
    assert flow["runs_hint_code"] == "run-unreadable"
    assert flow["run"]["run"] == "run-0001"
    assert flow["run"]["error_code"] == "run-unreadable" and flow["run"]["error"]
    assert flow["run"]["state_kind"] is None and flow["run"]["package_sha12"] is None
    cur = client.get(RUNS + "/current").json()
    assert cur["hint_code"] == "run-unreadable" and cur["package"] is None
    assert cur["run"] == {"run": "run-0001", "title": None, "state_kind": None,
                          "error_code": "run-unreadable", "error": err}
    card = client.get("/api/task-package/current").json()
    assert card["present"] is False and card["note_code"] == "run-unreadable"
    assert card["run"] == {"run": "run-0001", "title": None, "implicit": False}
    assert "не читается" in card["error"]
    assert task_channel.current_package() is None
    assert (_inbox(tmp_path) / sha / "package.zip").is_file(), "пакет цел"
    code, brief = task_channel.receive(_bytes(tmp_path), sha)
    assert code == 200 and brief["cosmos_run"] is None
    assert brief["run_note_code"] == "run-unreadable"
    # без указателя — прежние коды; форма GET current не расширяется
    (_root(tmp_path) / "current.json").write_text('{"run": null}', encoding="utf-8")
    assert cr.hint_code() == "no-current-run"
    assert client.get("/api/task-package/current").json() == {"present": False}


def test_lock_timeout_and_write_error_are_503_with_words_not_500(
        client, tmp_path, monkeypatch):
    """Срок файлового замка (TimeoutError) и ошибка записи (OSError) у
    пишущих ручек прогонов — 503 словами с кодом storage-busy /
    storage-io, а не «Internal Server Error» без слов."""
    import contextlib
    from webapp import cosmos_runs as cr
    assert client.post(RUNS, json={"title": "первый"}).status_code == 200

    @contextlib.contextmanager
    def busy():
        raise TimeoutError("хранилище прогонов занято другим процессом дольше "
                           "30 с — действие отменено")
        yield                                            # pragma: no cover
    # своя область подмены: общий monkeypatch держит и среду фикстуры
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cr, "storage_lock", busy)
        for method, path, body in (("post", "", {"title": "x"}),
                                   ("post", "/init", None),
                                   ("put", "/current", {"run": "run-0001"}),
                                   ("put", "/order", {"order": ["run-0001"]}),
                                   ("post", "/archive", {"runs": ["run-0001"]}),
                                   ("put", "/run-0001/title", {"title": "y"})):
            kw = {"json": body} if body is not None else {}
            r = getattr(client, method)(RUNS + path, **kw)
            assert r.status_code == 503, (path, r.text)
            assert r.json()["reason_code"] == "storage-busy", path
            assert "занято другим процессом" in r.json()["detail"], path

    def broken(path, doc):
        raise OSError("стенд: диск заперт")
    monkeypatch.setattr(cr, "_write_json_atomic", broken)
    r = client.put(RUNS + "/run-0001/title", json={"title": "z"})
    assert r.status_code == 503 and r.json()["reason_code"] == "storage-io"
    assert "диск заперт" in r.json()["detail"]
    r = client.post(RUNS, json={"title": "ещё"})
    assert r.status_code == 503 and r.json()["reason_code"] == "storage-io"


def test_storage_off_words_name_the_real_cause(client, tmp_path, monkeypatch):
    """Код storage-off один, причины две: пустой CREW_COSMOS_RUNS_DIR при
    живом инбоксе и выключенный инбокс (CREW_TASK_INBOX) — слова отказа,
    список и GET current называют настоящую переменную (cause)."""
    from webapp import cosmos_runs as cr
    monkeypatch.setenv("CREW_COSMOS_RUNS_DIR", "")
    assert cr.storage_off_cause() == "runs-dir-empty"
    r = client.post(RUNS, json={"title": "x"})
    assert r.status_code == 409 and r.json()["reason_code"] == "storage-off"
    assert r.json()["cause"] == "runs-dir-empty"
    assert "CREW_COSMOS_RUNS_DIR" in r.json()["detail"]
    assert "CREW_TASK_INBOX" not in r.json()["detail"]
    lst = client.get(RUNS).json()
    assert lst["storage"] == {"state": "off", "legacy": None,
                              "cause": "runs-dir-empty"}
    assert client.get(RUNS + "/current").json()["storage"] == {
        "state": "off", "cause": "runs-dir-empty"}
    monkeypatch.delenv("CREW_COSMOS_RUNS_DIR")
    monkeypatch.setenv("CREW_TASK_INBOX", "")
    assert cr.storage_off_cause() == "inbox-off"
    r = client.post(RUNS, json={"title": "x"})
    assert r.status_code == 409 and r.json()["cause"] == "inbox-off"
    assert "CREW_TASK_INBOX" in r.json()["detail"]
    assert client.get(RUNS).json()["storage"]["cause"] == "inbox-off"
    monkeypatch.delenv("CREW_TASK_INBOX")
    assert cr.storage_off_cause() is None
    assert "cause" not in client.get(RUNS).json()["storage"]


def test_replay_route_runs_one_deep_check_inside_press_only(client, tmp_path,
                                                            monkeypatch):
    """Ручка повтора не повторяет полную проверку доступности: она —
    одна, в press под запуском; 202 несёт возврат press (attempt_id, task,
    state из state.json рабочего каталога) плюс attempt.

    Она же называет НАЖАВШЕГО (10.09.2026): актор строится из личности
    запроса теми же словами, что у ручки нажатия, иначе попытка-повтор
    писала бы «неизвестен: вызов без личности». Клиент стенда — режим
    разработчика площадки, отсюда «dev:<почта>»."""
    from webapp import package_runs as pr
    sha, _ = _receive(tmp_path)
    wd = tmp_path / "data-root" / "package-runs" / sha
    _write_state(wd, [_att("A1")])
    calls = {"avail": 0, "press": 0}
    real_avail = pr.replay_availability

    def counting_avail(*a, **k):
        calls["avail"] += 1
        return real_avail(*a, **k)
    monkeypatch.setattr(pr, "replay_availability", counting_avail)
    seen: dict = {}

    def fake_press(task, **kw):
        calls["press"] += 1
        seen["actor"] = kw.get("actor")
        assert kw["replay"] == {"mode": "journal", "run": "run-0001",
                                "source_attempt": "A1"}
        return {"attempt_id": "A2", "run": "run-0001",
                "workdir": "package-runs/" + sha, "state": "кнопка: идёт",
                "done": False, "mode": "journal", "source_attempt": "A1",
                "task": task}
    monkeypatch.setattr(pr, "press", fake_press)
    r = client.post(RUNS + "/run-0001/attempts/A1/replay", json={"mode": "journal"})
    assert r.status_code == 202, r.text
    assert r.json()["attempt"] == "A2" and r.json()["attempt_id"] == "A2"
    assert r.json()["state"] == "кнопка: идёт" and r.json()["task"] == "pairing"
    assert calls == {"avail": 0, "press": 1}, "полная проверка — только в press"
    assert seen["actor"] == "dev:" + ADMIN, seen


def test_run_scoped_file_and_export_links_for_second_holder_and_old_package(
        client, tmp_path):
    from webapp import package_runs as pr
    sha, _ = _receive(tmp_path)
    client.post(RUNS, json={"title": "второй"})
    _receive(tmp_path)
    sub = tmp_path / "data-root" / "package-runs" / sha / "runs" / "run-0002"
    _write_state(sub, [_att("B1", "rostering", verdict="допустимо",
                            catalog="attempts/B1/button")])
    out = sub / "attempts" / "B1" / "button"
    out.mkdir(parents=True)
    (out / "delivery.json").write_text(json.dumps({"verdict": "допустимо"}),
                                       encoding="utf-8")
    (out / "llm-exchanges").mkdir()
    (out / "llm-exchanges" / "index.jsonl").write_bytes(
        b'{"n": 1, "role": "certifier", "request_sha256": "x"}\n')
    (out / "llm-exchanges" / "001-certifier.request.txt").write_bytes(b"prompt\r\n")
    (out / "llm-exchanges" / "001-certifier.reply.txt").write_bytes(b"ok\n")
    card = client.get(RUNS + "/run-0002").json()
    att = card["attempts"][0]
    assert att["id"] == "B1" and att["workdir"].endswith("runs/run-0002")
    href = RUNS + "/run-0002/attempts/B1/file/выдача-кнопки"
    assert {"alias": "выдача-кнопки", "href": href} in att["files"]
    assert client.get(href).json() == {"verdict": "допустимо"}
    assert att["exchanges_href"] == RUNS + "/run-0002/attempts/B1/exchanges"
    r = client.get(att["exchanges_href"])
    assert r.status_code == 200 and r.json()["rows"][0]["n"] == 1
    r = client.get(att["exchanges_href"] + "/1")
    assert r.json() == {"n": 1, "role": "certifier", "request": "prompt\r\n",
                        "reply": "ok\n", "error": None}
    assert client.get(att["exchanges_href"] + "/2").status_code == 404
    r = client.get(RUNS + "/run-0002/attempts/B1/file/обмены-с-моделью")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    # первый прогон второго держателя не видит
    assert client.get(RUNS + "/run-0001").json()["attempts"] == []
    assert client.get(RUNS + "/run-0001/attempts/B1").status_code == 404
    # выгрузки области прогона — через package_runs с wd= (рабочий
    # каталог попытки, не текущий пакет)
    r = client.get(att["exports"]["index_href"])
    assert r.status_code == 200 and r.json()["run"] == "run-0002"
    r = client.get(att["exports"]["index_href"] + "/roster/csv")
    assert r.status_code in (200, 404, 409)


# --------------------------------------------------------------- схема

def test_flow_carries_run_hint_and_idle_blocks_for_empty_run(client, tmp_path):
    st = client.get("/api/flow").json()
    assert st["run"] is None and st["runs_hint_code"] == "storage-not-initialized"
    client.post(RUNS, json={"title": "пустой"})
    st = client.get("/api/flow").json()
    assert st["run"]["run"] == "run-0001" and st["run"]["title"] == "пустой"
    assert st["run"]["state_kind"] == "new" and st["runs_hint_code"] is None
    assert [b["id"] for b in st["blocks"]] == [
        "vmp", "conform", "pairing.pool", "pairing.select", "accept",
        "pair.src", "roster", "srccheck", "roster.src", "out"]
    assert all(b["status"] == "idle" for b in st["blocks"])
    assert st["blocks"][0]["params"] == {"run": "run-0001"}
    assert st["active"] is None
    client.post(RUNS, json={"make_current": False})
    (_root(tmp_path) / "current.json").unlink()
    st = client.get("/api/flow").json()
    assert st["run"] is None and st["runs_hint_code"] == "no-current-run"


def test_flow_src_filters_by_package_sha(client, tmp_path):
    from webapp import flow
    sha, _ = _receive(tmp_path)
    wd = tmp_path / "data-root" / "package-runs" / sha
    _write_state(wd, [_att("A1")])
    (wd / "button-pairing").mkdir(parents=True)
    (wd / "button-pairing" / "delivery.json").write_text(
        json.dumps({"verdict": "пейринг построен"}), encoding="utf-8")
    snap = tmp_path / "data-root" / "source-check" / "pairing"
    snap.mkdir(parents=True)
    snap_doc = {"status": "done", "run": "run-9001", "at": "2026-09-07T10:00:00",
                "counts": {"violated": 0, "satisfied": 4},
                "package_sha": "f" * 64, "cosmos_run": "run-0009"}
    (snap / "last.json").write_text("﻿" + json.dumps(snap_doc), encoding="utf-8")
    by = {b["id"]: b for b in flow._press_blocks()}
    assert by["pair.src"]["status"] == "waiting", "снимок чужого пакета отсечён"
    snap_doc["package_sha"] = sha
    (snap / "last.json").write_text(json.dumps(snap_doc), encoding="utf-8")
    by = {b["id"]: b for b in flow._press_blocks()}
    assert by["pair.src"]["status"] == "done" and by["pair.src"]["params"]["checks"] == 4
    assert by["vmp"]["params"]["run"] == "run-0001"
    got = flow._source_check_state("pairing")
    assert got["package_sha"] == sha and got["cosmos_run"] == "run-0009"
    lst = client.get(RUNS).json()["runs"][0]
    assert lst["checks"]["idak"]["pairing"] is None, "чужой прогон не приписан"
    snap_doc["cosmos_run"] = "run-0001"
    (snap / "last.json").write_text(json.dumps(snap_doc), encoding="utf-8")
    assert client.get(RUNS).json()["runs"][0]["checks"]["idak"]["pairing"] == "ok"


def test_press_blocks_legacy_when_no_run_no_package(client, tmp_path):
    from webapp import flow
    client.post(RUNS, json={"make_current": False})
    assert flow._press_blocks() is None
    assert client.get("/api/flow").json()["runs_hint_code"] == "no-current-run"


def test_init_button_is_idempotent_and_current_state_shape(client, tmp_path):
    r = client.post(RUNS + "/init")
    assert r.status_code == 200 and r.json()["initialized"] is True
    assert r.json()["adoption"]["runs_created"] == 0
    assert client.post(RUNS + "/init").json()["initialized"] is False
    cur = client.get(RUNS + "/current").json()
    assert cur == {"run": None, "package": None, "storage": {"state": "ready"},
                   "env_package": False, "hint_code": "no-current-run"}


def test_storage_off_refuses_writes_with_code(client, tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_COSMOS_RUNS_DIR", "")
    for method, path, body in (("post", "", {"title": "x"}),
                               ("post", "/init", None),
                               ("put", "/current", {"run": "run-0001"}),
                               ("post", "/archive", {"runs": ["run-0001"]}),
                               ("put", "/order", {"order": ["run-0001"]})):
        kw = {"json": body} if body is not None else {}
        r = getattr(client, method)(RUNS + path, **kw)
        assert r.status_code == 409 and r.json()["reason_code"] == "storage-off", path
    lst = client.get(RUNS).json()
    assert lst["storage"]["state"] == "off" and lst["inbox_disabled"]
    # инбокс жив, а прогоны выключены: приём проходит, квитанция честна
    sha, brief = _receive(tmp_path)
    assert brief["cosmos_run"] is None and brief["run_note_code"] == "storage-off"
    assert _rj(_inbox(tmp_path) / "current.json")["sha"] == sha
