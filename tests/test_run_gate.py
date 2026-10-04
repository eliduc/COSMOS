# -*- coding: utf-8 -*-
"""Гейт «от пакета» (webapp/run_gate.py): нажатие идёт только у
ОБЪЯВЛЕННОГО прогона.

Основание — проект `final-report/PROEKT-ZAPIS-O-PROGONE-CLD.md`, раздел
10 (половина COSMOS шага 2). Судится ровно то, ради чего гейт заведён:

  * пакет с номером записи — прогон объявлен хозяином: рядом с нажатиями
    ложится фрагмент COSMOS с головой ссылкой и объявлением СВОЕЙ среды,
    нажатие разрешено, а повторное обращение фрагмент не переписывает;
  * сменившиеся после объявления модели по ролям — отказ
    `models-changed` словами «объявлено … / на деле …»: голова записи
    неизменна, и молча считать другой моделью нельзя;
  * пакет без номера записи — прогон ЧАСТНЫЙ: нажатие отказывает
    `run-not-declared`, пока прогон не объявлен ручкой
    `POST /api/run/declare`; объявление ложится записью по словарю
    (`vmp/run_record.py`), повтор — отказ `already-declared`, слово вне
    словаря — `declaration-invalid`;
  * карточка `GET /api/run/current` отвечает у обеих ветвей и несёт
    предзаполнение формы объявления;
  * машинные маршруты записи открыты по токену канала и закрыты без
    него (fail-closed), как маршруты нажатия.

Литералов среды в файле нет (сторож tests/test_no_environment_literals):
каталог данных — `tmp_path`, личность — `_stand_identity.ADMIN_EMAIL`,
пакет — синтетический (сборка `cdpm.export_task_package` на фикстурах
стенда), служб не поднимается.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from _stand_identity import ADMIN_EMAIL

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))

from tests.test_task_channel import _pkg_bytes            # noqa: E402
from vmp import run_record as rr                          # noqa: E402

ADMIN = ADMIN_EMAIL
TOKEN = "test-run-gate-token-2026"
DECLARE = "/api/run/declare"
CURRENT = "/api/run/current"
PRESS = "/api/task-package/current/press"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    """Каталог данных и носители площадки — свои у каждого теста.

    Площадка прогона и адрес записи хозяина — переменные среды: их
    значение на машине разработчика сделало бы прогон
    недетерминированным (правило «в тестах нет литералов среды»)."""
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.delenv("CREW_TASK_INBOX", raising=False)
    monkeypatch.delenv("CREW_TASK_PACKAGE", raising=False)
    monkeypatch.delenv("CREW_VMP_REGISTRY", raising=False)
    monkeypatch.delenv("CREW_SOLVER_URL", raising=False)
    monkeypatch.delenv("CREW_CHANNEL_TOKEN", raising=False)
    monkeypatch.delenv("CREW_CONTOUR", raising=False)
    monkeypatch.delenv("CREW_CDPM_RECORD_API", raising=False)
    from webapp import flow, task_channel
    monkeypatch.setitem(flow._READY_CACHE, "key", None)
    monkeypatch.setitem(task_channel._CARD_CACHE, "key", None)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """TestClient в режиме разработчика (образец tests/test_package_runs)."""
    from webapp import users
    cfg = tmp_path_factory.mktemp("run-gate-auth")
    mp = pytest.MonkeyPatch()
    mp.setenv("CREW_AUTH_MODE", "dev")
    mp.setenv("CREW_DEV_USER", ADMIN)
    mp.delenv("CREW_ACCESS_AUD", raising=False)
    mp.setenv("COSMOS_SKIP_CONFIG_MIGRATION", "1")
    mp.setattr(users, "USERS_DB_PATH", cfg / "users.db")
    users.ensure_db()
    import importlib
    from webapp import app as app_module
    importlib.reload(app_module)
    from fastapi.testclient import TestClient
    try:
        with TestClient(app_module.app) as c:
            yield c
    finally:
        mp.undo()


def _without_record(body: bytes) -> bytes:
    """Тот же пакет БЕЗ номера записи в манифесте — вход частного
    прогона от готового ВМП. Перечень `files` манифеста не трогается:
    суммы файлов остаются верными, гейт производителя зелен."""
    src = zipfile.ZipFile(io.BytesIO(body))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            raw = src.read(info.filename)
            if info.filename == "manifest.json":
                man = json.loads(raw)
                man.pop("record_id", None)
                man.pop("declaration_sha256", None)
                raw = (json.dumps(man, ensure_ascii=False, indent=2)
                       + "\n").encode("utf-8")
            z.writestr(info, raw)
    return out.getvalue()


def _receive(tmp_path, *, declared: bool = True) -> str:
    """Пакет принимается каналом и становится текущим; возвращает sha."""
    from webapp import task_channel
    body = _pkg_bytes(tmp_path / "src")
    if not declared:
        body = _without_record(body)
    sha = hashlib.sha256(body).hexdigest()
    code, brief = task_channel.receive(body, sha)
    assert code in (200, 201, 202), brief
    return sha


def _upload(client, tmp_path, *, declared: bool = True) -> str:
    """Тот же пакет — ручкой загрузки (путь человека)."""
    body = _pkg_bytes(tmp_path / "src")
    if not declared:
        body = _without_record(body)
    r = client.post("/api/task-package/upload",
                    files={"file": ("h0.zip", body, "application/zip")})
    assert r.status_code in (200, 201), r.text
    return hashlib.sha256(body).hexdigest()


def _wd(sha: str) -> Path:
    from webapp import package_runs as pr
    return pr.workdir_for(sha)


# ------------------------------------------------------- полный прогон

def test_полный_прогон_фрагмент_ложится_и_нажатие_разрешено(tmp_path):
    """Пакет с номером записи: голова наследуется ссылкой, фрагмент
    COSMOS объявляет свою среду, нажатие разрешено."""
    from webapp import run_gate
    sha = _receive(tmp_path)
    fragment = run_gate.check_press(task="pairing", actor="стенд")
    assert fragment["schema"] == run_gate.FRAGMENT_SCHEMA
    head = fragment["head"]
    assert head["record_id"] and head["owner"] == run_gate.OWNER_MANIFEST
    assert head["source_run"] == head["record_id"]
    assert len(head["declaration_sha256"]) == 64
    # Голова у пакета без адреса ручек записи называет пробел кодом, а
    # не молчит: сшивать некуда, и читатель это видит.
    assert head["record_api"] is None
    assert head["record_api_code"] == "record-api-missing"
    env = fragment["cosmos_environment"]
    assert env["catalog"]["version"] == env["bindings"]["catalog_version"]
    roles = {m["role"] for m in env["models"]}
    assert roles == {run_gate.ROLE_CERTIFIER, run_gate.ROLE_SOURCE_READER}
    assert any(s["task"] == "rostering" for s in env["solvers"])
    assert env["libraries"]["packages_sha256"]
    # Адрес решателя площадкой не задан — это предупреждение сверки, а
    # не отказ: доступность решателя судит само нажатие.
    check = fragment["environment_check"]
    assert check["state"] in (run_gate.CHECK_OK, run_gate.CHECK_WARN)
    assert (_wd(sha) / run_gate.FRAGMENT_FILE).is_file()
    # Повторное обращение гейта фрагмент не переписывает (голова
    # неизменна), исход сверки — обновляет.
    again = run_gate.check_press(task="rostering", actor="стенд")
    assert again["declared_at"] == fragment["declared_at"]
    assert again["environment_check"]["at"]


def test_смена_модели_после_объявления_отказ_словами(tmp_path, monkeypatch):
    """Сертификатор сменился после объявления — нажатия нет: отказ
    `models-changed` называет обе стороны."""
    from webapp import llm_backend, run_gate
    monkeypatch.setattr(llm_backend, "ollama_model", lambda: "модель-до")
    _receive(tmp_path)
    run_gate.check_press(task="pairing", actor="стенд")
    monkeypatch.setattr(llm_backend, "ollama_model", lambda: "модель-после")
    with pytest.raises(run_gate.RunGateError) as e:
        run_gate.check_press(task="pairing", actor="стенд")
    assert e.value.code == run_gate.CODE_MODELS
    words = " ".join(e.value.words)
    assert "объявлено" in words and "на деле" in words
    assert "модель-до" in words and "модель-после" in words
    assert e.value.status == 409


# ------------------------------------------------------ частный прогон

def test_частный_прогон_нажатие_отказывает_до_объявления(tmp_path):
    from webapp import run_gate
    _receive(tmp_path, declared=False)
    with pytest.raises(run_gate.RunGateError) as e:
        run_gate.check_press(task="pairing", actor="стенд")
    assert e.value.code == run_gate.CODE_NOT_DECLARED
    assert "прогон не объявлен" in e.value.text()


def test_объявление_от_пакета_пишет_запись_по_словарю(tmp_path):
    """`declare` чеканит номер, собирает голову из пакета и среды и
    кладёт `run-record.json` в папку нажатий: хозяин — COSMOS."""
    from webapp import run_gate
    sha = _receive(tmp_path, declared=False)
    # Слово вне словаря — отказ 422 ДО рождения записи.
    with pytest.raises(run_gate.RunGateError) as e:
        run_gate.declare({"purpose": "как получится"}, "стенд")
    assert e.value.code == run_gate.CODE_INVALID and e.value.status == 422
    assert not (_wd(sha) / rr.RECORD_FILE).exists()

    got = run_gate.declare({"purpose": "исследование"}, "стенд")
    record = got["record"]
    assert rr.validate(record) == []
    d = record["declaration"]
    assert d["purpose"] == "исследование"
    assert d["contour"]["name"] == "local"          # площадка не задана
    assert d["created_by"] == "стенд"
    assert d["lineage"]["package_sha256"] == sha
    assert d["lineage"]["source_run"]
    assert d["rules_documents"], "документы правил прогона не названы"
    assert d["catalog"]["version"] and d["bindings"]["catalog_version"]
    assert {m["role"] for m in d["models"]} == {run_gate.ROLE_CERTIFIER,
                                                run_gate.ROLE_SOURCE_READER}
    assert d["solvers"] and all(s["spent_limit_s"] for s in d["solvers"])
    assert record["results"]["state"] == rr.STATE_DECLARED
    on_disk = rr.load(_wd(sha))
    assert on_disk["declaration_sha256"] == record["declaration_sha256"]
    # После объявления нажатие разрешено, а голова — своя.
    fragment = run_gate.check_press(task="pairing", actor="стенд")
    assert fragment["head"]["owner"] == run_gate.OWNER_COSMOS
    assert fragment["head"]["record_id"] == d["run_id"]
    # Повтор объявления того же пакета — отказ: голова неизменна.
    with pytest.raises(run_gate.RunGateError) as e:
        run_gate.declare({}, "стенд")
    assert e.value.code == run_gate.CODE_ALREADY


def test_объявление_без_пакета_отказывает_словами(tmp_path):
    from webapp import run_gate
    with pytest.raises(run_gate.RunGateError) as e:
        run_gate.declare({}, "стенд")
    assert e.value.code == run_gate.CODE_PACKAGE_MISSING


def test_площадка_и_адрес_записи_берутся_из_переменных(tmp_path, monkeypatch):
    """Площадка прогона и адрес ручек записи — носители площадки:
    названы переменными, а не выдуманы по имени машины."""
    from webapp import run_gate
    monkeypatch.setenv("CREW_CONTOUR", "test")
    monkeypatch.setenv("CREW_CDPM_RECORD_API", "http://панель.invalid")
    _receive(tmp_path)
    head = run_gate.head_of(
        {"record_id": "run-0001", "source_run": "run-0001"})
    assert head["record_api"] == "http://панель.invalid/api/runs/run-0001/record"
    assert head["record_api_code"] == "record-api-from-site"
    assert run_gate.site_contour() == "test"
    monkeypatch.setenv("CREW_CONTOUR", "телепатия")
    assert run_gate.site_contour() == "local"       # слово вне словаря


# ------------------------------------------------------------- ручки

def test_ручка_current_у_необъявленного_прогона(client, tmp_path):
    from webapp import run_gate
    _upload(client, tmp_path, declared=False)
    r = client.get(CURRENT)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["present"] and got["declared"] is False
    assert got["declare_reason_code"] == run_gate.CODE_NOT_DECLARED
    assert got["state"] == "не объявлен"
    form = got["form"]
    assert form["purposes"] == list(rr.PURPOSES)
    assert form["contours"] == list(rr.CONTOURS)
    assert form["contour"] == "local"
    assert {m["role"] for m in form["models"]} == {
        run_gate.ROLE_CERTIFIER, run_gate.ROLE_SOURCE_READER}
    assert form["planned_stages"] == list(run_gate.DEFAULT_PLANNED_STAGES)


def test_ручка_declare_и_current_у_объявленного_прогона(client, tmp_path):
    from webapp import run_gate
    _upload(client, tmp_path, declared=False)
    r = client.post(DECLARE, json={"purpose": "демонстрация",
                                   "created_by": "стенд"})
    assert r.status_code == 200, r.text
    record = r.json()["record"]
    assert r.json()["ok"] is True and rr.validate(record) == []
    r2 = client.post(DECLARE, json={})
    assert r2.status_code == 409 and r2.json()["code"] == run_gate.CODE_ALREADY
    assert r2.json()["ok"] is False
    r3 = client.get(CURRENT)
    assert r3.status_code == 200, r3.text
    got = r3.json()
    assert got["declared"] is True
    assert got["head"]["owner"] == run_gate.OWNER_COSMOS
    assert got["record"]["declaration"]["run_id"] \
        == record["declaration"]["run_id"]
    assert got["record_source"] == "local"
    assert got["state"] == rr.STATE_DECLARED
    assert got["stitch"]["mode"] == "local"
    assert got["cosmos"]["environment"]["catalog"]["version"]


def test_ручка_нажатия_отказывает_необъявленному_прогону(client, tmp_path,
                                                        monkeypatch):
    """Отказ гейта — 409 в том же виде, что отказы нажатия: `detail`
    строкой (её читают сегодняшние клиенты) плюс код и слова. Само
    нажатие при этом НЕ зовётся."""
    from webapp import package_runs as pr, run_gate
    _upload(client, tmp_path, declared=False)
    called = []
    monkeypatch.setattr(pr, "press", lambda *a, **k: called.append(1))
    r = client.post(PRESS + "?task=pairing")
    assert r.status_code == 409, r.text
    got = r.json()
    assert got["code"] == run_gate.CODE_NOT_DECLARED
    assert "прогон не объявлен" in got["detail"]
    assert got["words"] and isinstance(got["words"], list)
    assert not called, "нажатие пошло у необъявленного прогона"


def test_ручка_нажатия_у_объявленного_прогона_зовёт_кнопку(client, tmp_path,
                                                          monkeypatch):
    from webapp import package_runs as pr
    sha = _upload(client, tmp_path)
    seen: dict = {}
    monkeypatch.setattr(pr, "press", lambda task, **kw:
                        seen.update(task=task, **kw) or {"present": True})
    r = client.post(PRESS + "?task=pairing")
    assert r.status_code == 200, r.text
    assert seen["task"] == "pairing"
    from webapp import run_gate
    assert (_wd(sha) / run_gate.FRAGMENT_FILE).is_file()


def test_машинный_вход_к_ручкам_записи(client, tmp_path, monkeypatch):
    """Раннер наборов и панель площадки ходят к записи без сессии — по
    токену канала; чужой токен — 403, выключенный канал — 403."""
    from webapp import app as app_module
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    ok = {"X-Channel-Token": TOKEN}
    bad = {"X-Channel-Token": "wrong-token"}
    for method, path in (("POST", "/api/run/declare"),
                         ("GET", "/api/run/current"),
                         ("POST", "/api/run/stitch"),
                         ("POST", "/api/source-check/run"),
                         ("GET", "/api/source-check/last"),
                         ("GET", "/api/cosmos-runs/run-0001/record")):
        assert app_module._machine_route(method, path), (method, path)
    _upload(client, tmp_path, declared=False)
    assert client.get(CURRENT, headers=ok).status_code == 200
    assert client.get(CURRENT, headers=bad).status_code == 403
    assert client.get("/api/source-check/last", headers=ok).status_code == 200
    assert client.get("/api/source-check/last", headers=bad).status_code == 403
    r = client.post(DECLARE, json={"purpose": "регресс"}, headers=ok)
    assert r.status_code == 200, r.text
    # Актор машинного объявления — канал по отпечатку токена, сам токен
    # в запись не уходит.
    created = r.json()["record"]["declaration"]["created_by"]
    assert created.startswith("channel:") and TOKEN not in created
    monkeypatch.delenv("CREW_CHANNEL_TOKEN")
    assert client.get(CURRENT, headers=ok).status_code == 403


def test_ручка_записи_по_номеру_прогона(client, tmp_path):
    """`GET /api/cosmos-runs/<run>/record` — та же запись по номеру
    прогона COSMOS (её читают машинные читатели)."""
    from webapp import cosmos_runs
    _upload(client, tmp_path, declared=False)
    r = client.post(DECLARE, json={"purpose": "регресс"})
    assert r.status_code == 200, r.text
    run = cosmos_runs.current_run_id()
    assert run, "прогон COSMOS не заведён приёмом пакета"
    got = client.get("/api/cosmos-runs/%s/record" % run)
    assert got.status_code == 200, got.text
    doc = got.json()
    assert doc["run"] == run
    assert doc["record"]["declaration"]["run_id"] \
        == r.json()["record"]["declaration"]["run_id"]
    assert doc["stitch"]["mode"] == "local"
    assert client.get("/api/cosmos-runs/run-9999/record").status_code == 404
