# -*- coding: utf-8 -*-
"""Ручки выгрузок решений пакета (webapp/app.py + webapp/package_runs.py,
слово владельца 06.09.2026): указатель доступности и файлы CSV/XLSX/JSON/
iCal/Meridian по артефактам последней выдачи либо снимка попытки.

Известные ответы: без пакета — {present: false}; с пакетом без выдач —
все форматы недоступны с причинами словами; артефакты пейринга — CSV с
BOM и Content-Disposition, XLSX, JSON, Meridian (200 при certified);
несертифицированное — Meridian 409 словами, с allow_uncertified=1 — 200 и
X-COSMOS-Certified: false, имя с «-uncertified»; ростер — iCal, ?employee,
Meridian 409 на сквозном ростере, ?path, ?attempt; чужой solution/fmt —
404 с перечнем; выдача-остановка — 409; path вне словаря — 400; токен
канала — 403/200; путь /export входит в _MACHINE_PREFIXES, и в режиме
token без сессии запрос с токеном доходит до обработчика (не 401);
ссылки указателя — с префиксом /api, панель их не префиксует."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from _stand_identity import ADMIN_EMAIL

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))

from tests import test_vmp_export_solutions as world  # noqa: E402
from tests.test_task_channel import _pkg_bytes  # noqa: E402

ADMIN = ADMIN_EMAIL
EXPORT = "/api/task-package/current/export"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.delenv("CREW_TASK_INBOX", raising=False)
    monkeypatch.delenv("CREW_TASK_PACKAGE", raising=False)
    from webapp import flow, task_channel
    monkeypatch.setitem(flow._READY_CACHE, "key", None)
    monkeypatch.setitem(task_channel._CARD_CACHE, "key", None)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from webapp import users
    cfg = tmp_path_factory.mktemp("export-auth")
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


def _make_current(tmp_path) -> str:
    import hashlib
    from webapp import task_channel
    body = _pkg_bytes(tmp_path / "src")
    sha = hashlib.sha256(body).hexdigest()
    code, brief = task_channel.receive(body, sha)
    assert code in (200, 201, 202), brief
    return sha


def _wj(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _write_pairing(root: Path, certified=True, delivery=None) -> None:
    """Артефакты выдачи пейринга — как их пишет кнопка (button-pairing/)."""
    from webapp import package_runs as pr
    d = root / "button-pairing"
    _wj(d / "delivery.json", delivery or world.pairing_delivery(certified))
    _wj(d / "builder-catalog.json", world.catalog())
    _wj(d / "builder-payload.json", world.payload())
    _wj(d / "source-rules.json", world.rules())
    _wj(d / "certificate.json", world.certificate())
    shutil.copy(Path(pr.current()["path"]), d / "package.zip")


def _write_roster(root: Path, sub="button-on-pairing", cold=False, certified=True) -> None:
    """Артефакты выдачи ростеринга; SHA каталога пейринга в выдаче — от
    настоящих байт button-pairing/builder-catalog.json каталога пакета
    (иначе поиск каталога того же прогона честно откажет)."""
    import hashlib
    from webapp import package_runs as pr
    d = root / sub
    sol = world.roster_solution()
    if cold:
        for a in sol["assignments"]:
            a.pop("pairing_id")
    delivery = world.roster_delivery(certified)
    wd = pr.workdir_for(hashlib.sha256(Path(pr.current()["path"]).read_bytes()).hexdigest())
    cat_p = wd / "button-pairing" / "builder-catalog.json"
    if cat_p.is_file():
        digest = hashlib.sha256(cat_p.read_bytes()).hexdigest()
        delivery["pairing_catalog_sha256"] = digest
        delivery["pairing_selection"]["catalog_sha256"] = digest
    _wj(d / "delivery.json", delivery)
    _wj(d / "solution.json", sol)
    _wj(d / "solved.json", world.solved())
    _wj(d / "certificate.json", world.certificate())
    shutil.copy(Path(pr.current()["path"]), d / "package.zip")


def test_index_without_package_and_without_deliveries(client, tmp_path):
    r = client.get(EXPORT)
    assert r.status_code == 200 and r.json() == {"present": False}
    _make_current(tmp_path)
    ix = client.get(EXPORT).json()
    assert ix["present"] and ix["roster_path"] is None
    for kind, words in (("pairing", "нажмите «Пейринг»"), ("roster", "нажмите «Ростеринг»")):
        sol = ix["solutions"][kind]
        assert sol["present"] is False and words in sol["reason"]
        assert all(v["ok"] is False and words in v["reason"] for v in sol["formats"].values())
    # файл без выдачи — 404 словами
    r = client.get(EXPORT + "/pairing/csv")
    assert r.status_code == 404 and "нажмите «Пейринг»" in r.text


def test_pairing_files_headers_and_uncertified_rule(client, tmp_path):
    from webapp import package_runs as pr
    sha = _make_current(tmp_path)
    wd = pr.workdir_for(sha)
    _write_pairing(wd)
    ix = client.get(EXPORT).json()
    pairing = ix["solutions"]["pairing"]
    assert pairing["certified"] is True and pairing["formats"]["meridian"]["ok"] is True
    assert pairing["formats"]["csv"]["href"] == EXPORT + "/pairing/csv"
    # ростера ещё нет — недоступен с причиной словами
    roster = ix["solutions"]["roster"]
    assert roster["present"] is False and "ростеринга ещё нет" in roster["reason"]
    r = client.get(EXPORT + "/pairing/csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.content.startswith("﻿".encode("utf-8"))
    assert r.headers["content-disposition"] == \
        'attachment; filename="cosmos-pairing-%s.csv"' % sha[:12]
    assert r.headers["x-cosmos-certified"] == "true"
    assert r.headers["x-cosmos-package"] == sha and r.headers["x-cosmos-solver-run"] == "run-p-1"
    assert r.headers["x-cosmos-time-scale"] in ("unknown", "base-local;zone=Europe/Moscow")
    r = client.get(EXPORT + "/pairing/xlsx")
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"]
    assert r.content[:2] == b"PK"
    r = client.get(EXPORT + "/pairing/json")
    assert r.status_code == 200 and r.json()["kind"] == "cosmos-pairing-export/1"
    r = client.get(EXPORT + "/pairing/meridian")
    assert r.status_code == 200 and r.json()["type"] == "pairing"
    assert r.headers["content-disposition"].endswith('-meridian.json"')
    assert "x-cosmos-form-status" not in r.headers
    # детерминированность через ручку
    assert client.get(EXPORT + "/pairing/xlsx").content == \
        client.get(EXPORT + "/pairing/xlsx").content
    # несертифицированное: рабочие виды с пометкой, Meridian — 409 / явно
    _write_pairing(wd, certified=False)
    ix = client.get(EXPORT).json()["solutions"]["pairing"]
    assert ix["certified"] is False and ix["formats"]["csv"]["ok"] is True
    assert ix["formats"]["meridian"]["ok"] is False and ix["formats"]["meridian"]["needs_confirm"]
    r = client.get(EXPORT + "/pairing/csv")
    assert r.status_code == 200 and r.headers["x-cosmos-certified"] == "false"
    assert "-uncertified.csv" in r.headers["content-disposition"]
    r = client.get(EXPORT + "/pairing/meridian")
    assert r.status_code == 409 and "allow_uncertified" in r.text
    r = client.get(EXPORT + "/pairing/meridian?allow_uncertified=1")
    assert r.status_code == 200 and r.headers["x-cosmos-certified"] == "false"
    assert "-uncertified-meridian.json" in r.headers["content-disposition"]
    assert "НЕ СЕРТИФИЦИРОВАНО" in r.json()["description"]


def test_meridian_parameters_utc_roles_ids(client, tmp_path):
    from webapp import package_runs as pr
    sha = _make_current(tmp_path)
    _write_pairing(pr.workdir_for(sha))
    base = client.get(EXPORT + "/pairing/meridian").json()
    assert base["datasetId"] == sha
    r = client.get(EXPORT + "/pairing/meridian?dataset_id=DS-9&preset_id=PR-9&roles=source")
    assert r.status_code == 200
    doc = r.json()
    assert (doc["datasetId"], doc["presetId"]) == ("DS-9", "PR-9")
    assert {m["metricCode"]: m["metricValue"] for m in doc["metrics"]}["ROLE_ALPHABET"] != "Р/П"
    assert client.get(EXPORT + "/pairing/meridian?roles=x").status_code == 400
    r = client.get(EXPORT + "/pairing/meridian?utc=1")
    assert r.status_code == 200
    # у стендового пакета шкала не объявлена — перевод честно не выполнен
    assert "utc" in r.headers["x-cosmos-time-scale"] or \
        "записано без перевода" in r.json()["description"]


def test_roster_files_paths_attempt_and_refusals(client, tmp_path):
    from webapp import package_runs as pr
    sha = _make_current(tmp_path)
    wd = pr.workdir_for(sha)
    _write_pairing(wd)
    # сквозной ростер (button/) — iCal есть, Meridian отказывает словами
    _write_roster(wd, "button", cold=True)
    ix = client.get(EXPORT).json()
    assert ix["roster_path"] == "cold"
    roster = ix["solutions"]["roster"]
    assert roster["formats"]["ics"]["ok"] is True
    assert "без слоя цепочек" in roster["formats"]["meridian"]["reason"]
    assert roster["employees"] == ["H01", "H02", "H03", "H04", "H05"]
    r = client.get(EXPORT + "/roster/ics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    assert r.text.count("BEGIN:VEVENT") == 16
    r = client.get(EXPORT + "/roster/ics?employee=H01")
    assert r.status_code == 200 and r.text.count("BEGIN:VEVENT") == 3
    assert r.headers["content-disposition"] == \
        'attachment; filename="cosmos-roster-%s-H01.ics"' % sha[:12]
    r = client.get(EXPORT + "/roster/ics?employee=H99")
    assert r.status_code == 404 and "не найден" in r.text
    r = client.get(EXPORT + "/roster/meridian")
    assert r.status_code == 409 and "без слоя цепочек" in r.text
    # ростер на пейринге — новейшая выдача; Meridian-ростер — проект формы
    _write_roster(wd, "button-on-pairing")
    ix = client.get(EXPORT).json()
    assert ix["roster_path"] == "on-pairing"
    assert ix["solutions"]["roster"]["formats"]["meridian"]["form_status"] == "draft"
    r = client.get(EXPORT + "/roster/meridian")
    assert r.status_code == 200 and r.headers["x-cosmos-form-status"] == "draft"
    assert r.json()["description"].startswith("ФОРМА РОСТЕРА — ПРОЕКТ")
    assert r.json()["pairings"][0]["pairingId"].startswith("COSMOS-abcdef123456-")
    for fmt, ctype in (("csv", "text/csv"), ("xlsx", "spreadsheetml"), ("json", "application/json")):
        r = client.get(EXPORT + "/roster/" + fmt)
        assert r.status_code == 200 and ctype in r.headers["content-type"], fmt
    # явный путь: cold — сквозной (Meridian 409), on-pairing — 200
    assert client.get(EXPORT + "/roster/meridian?path=cold").status_code == 409
    assert client.get(EXPORT + "/roster/meridian?path=on-pairing").status_code == 200
    assert client.get(EXPORT + "/roster/csv?path=hot").status_code == 400
    # снимок попытки
    _write_roster(wd / pr.ATTEMPTS_DIR / "A1", "button-on-pairing")
    r = client.get(EXPORT + "/roster/csv?attempt=A1")
    assert r.status_code == 200 and "-A1" in r.headers["content-disposition"]
    r = client.get(EXPORT + "/roster/csv?attempt=B2")
    assert r.status_code == 404 and "снимка попытки" in r.text
    r = client.get(EXPORT + "/pairing/csv?attempt=A1")
    assert r.status_code == 404 and "нет артефактов пейринга" in r.text
    # чужие solution / fmt — 404 с перечнем
    r = client.get(EXPORT + "/roster/mps")
    assert r.status_code == 404 and "перечень" in r.text and "ics" in r.text
    r = client.get(EXPORT + "/duty/csv")
    assert r.status_code == 404 and "pairing, roster" in r.text
    # выдача-остановка — 409
    d = world.pairing_delivery()
    d["verdict"] = "остановка: решатель отверг пакет"
    _write_pairing(wd, delivery=d)
    r = client.get(EXPORT + "/pairing/csv")
    assert r.status_code == 409 and "остановка" in r.text
    assert client.get(EXPORT).json()["solutions"]["pairing"]["formats"]["csv"]["ok"] is False


def test_panel_uses_server_href_without_second_api_prefix(client, tmp_path):
    """Ссылки ряда «Выгрузить»: указатель отдаёт href уже с /api, и клиент
    (flowExportRows / flowExportIcsPick в app.js) берёт его как есть —
    иначе получалось /api/api/… и 404 на каждом скачивании (ревью
    07.09.2026). Проверяются обе стороны: ответ сервера и текст статики."""
    from webapp import package_runs as pr
    sha = _make_current(tmp_path)
    wd = pr.workdir_for(sha)
    _write_pairing(wd)
    _write_roster(wd, "button-on-pairing")
    ix = client.get(EXPORT).json()
    for kind in ("pairing", "roster"):
        for fmt, info in ix["solutions"][kind]["formats"].items():
            assert info["href"] == "%s/%s/%s" % (EXPORT, kind, fmt)
            assert not info["href"].startswith("/api/api/")
    assert ix["solutions"]["roster"]["employees"] == ["H01", "H02", "H03", "H04", "H05"]
    # ссылка указателя открывается как есть (без второго префикса)
    r = client.get(ix["solutions"]["roster"]["formats"]["ics"]["href"] + "?employee=H01")
    assert r.status_code == 200 and r.text.count("BEGIN:VEVENT") == 3
    js = (HERE / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
    start = js.index("function flowExportRows(")
    body = js[start:js.index("function flowOpenBlockPanel(")]
    assert "API + (info.href" not in body and "API + info.href" not in body
    assert "info.href ||" in body and "icsInfo.href ||" in body
    assert 'API + "/task-package/current/export/' in body      # запасной путь — свой
    assert "flow.out.export.ics.pick" in body and "flow.out.export.ics.hint" not in js
    assert "?employee=" in body and "sol.employees" in body    # список из указателя


def test_run_scoped_export_of_second_holder_via_cosmos_runs_route(client, tmp_path):
    """Тот же пакет во втором прогоне COSMOS работает в подкаталоге
    runs/<run> (07.09.2026); выгрузка снимка его попытки идёт ручкой
    области прогона, а не через текущий пакет. До интеграции wd= в
    package_runs ручка честно отказывает кодом, не 500; артефакт по
    алиасу отдаётся в любом случае."""
    from webapp import cosmos_runs, package_runs as pr
    from webapp import task_channel
    sha = _make_current(tmp_path)
    assert cosmos_runs.current_run_id() == "run-0001"
    body = Path(pr.current()["path"]).read_bytes()
    cosmos_runs.create_run("второй")
    code, brief = task_channel.receive(body, sha)
    assert code == 200 and brief["cosmos_run"] == "run-0002"
    sub = pr.workdir_for(sha) / "runs" / "run-0002"
    assert cosmos_runs.read_run("run-0002")["packages"][0]["workdir"] == \
        "package-runs/%s/runs/run-0002" % sha
    _write_pairing(sub)
    _write_roster(sub / pr.ATTEMPTS_DIR / "A1", "button-on-pairing")
    (sub / "state.json").write_text(json.dumps({"attempts": [
        {"id": "A1", "task": "rostering", "done": True, "verdict": "допустимо",
         "pairing_selection": {"pairing_run_id": "run-p-1"},
         "catalog": "attempts/A1/button-on-pairing",
         "started_at": "2026-09-07T10:00:00"}], "done": True}),
        encoding="utf-8")
    card = client.get("/api/cosmos-runs/run-0002").json()
    att = card["attempts"][0]
    assert att["id"] == "A1" and att["kind"] == "rostering_on_pairing"
    r = client.get("/api/cosmos-runs/run-0002/attempts/A1/file/"
                   "выдача-кнопки-на-пейринге")
    assert r.status_code == 200 and r.json()["verdict"]
    href = att["exports"]["index_href"]
    assert href == "/api/cosmos-runs/run-0002/attempts/A1/export"
    r = client.get(href + "/roster/csv")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["x-cosmos-run"] == "run-0002"
    assert "-A1" in r.headers["content-disposition"]
    ix = client.get(href).json()
    assert ix["run"] == "run-0002" and ix["attempt"] == "A1"
    for kind, sol in ix["solutions"].items():
        for fmt, info in sol["formats"].items():
            assert info["href"] == "%s/%s/%s" % (href, kind, fmt)
    # первый прогон второго держателя не видит
    assert client.get("/api/cosmos-runs/run-0001").json()["attempts"] == []
    assert client.get("/api/cosmos-runs/run-0001/attempts/A1/file/"
                      "выдача-кнопки-на-пейринге").status_code == 404
    # прежняя ручка текущего пакета — как есть (экспорт по текущему пакету)
    assert client.get(EXPORT).status_code == 200


def test_channel_token_and_machine_prefix(client, tmp_path, monkeypatch):
    from webapp import app as app_module
    assert any(EXPORT.startswith(p) for p in app_module._MACHINE_PREFIXES)
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", "t-2026")
    ok, bad = {"X-Channel-Token": "t-2026"}, {"X-Channel-Token": "wrong"}
    assert client.get(EXPORT, headers=bad).status_code == 403
    assert client.get(EXPORT, headers=ok).json() == {"present": False}
    # Режим token (сессия обязательна): без куки и токена — 401 от
    # middleware; с токеном путь /export пропускается к обработчику, и
    # решает уже он (200 / 403), не редирект и не 401.
    monkeypatch.delenv("CREW_AUTH_MODE", raising=False)
    monkeypatch.delenv("CREW_ACCESS_AUD", raising=False)
    from webapp import auth
    assert auth.get_auth_mode() == "token"
    r = client.get(EXPORT, headers={})
    assert r.status_code == 401
    r = client.get(EXPORT, headers=ok)
    assert r.status_code == 200 and r.json() == {"present": False}
    r = client.get(EXPORT + "/pairing/csv", headers=ok)
    assert r.status_code == 404                       # обработчик: пакета нет
    assert client.get(EXPORT, headers=bad).status_code == 403
