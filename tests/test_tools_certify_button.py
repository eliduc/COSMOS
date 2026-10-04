# -*- coding: utf-8 -*-
"""Инструмент сертификации и клиент ручек COSMOS «Пакет постановки»
(граница владельца 04.09: расчёт и сертификация — у COSMOS).

Стенд — НАСТОЯЩЕЕ веб-приложение COSMOS в процессе (webapp.app, вход
dev-режимом): маршруты, параметры и форма ответов — живые; подменяются
только глубины (приём канала task_channel.receive, нажатие/состояние/
артефакты package_runs) состоянием в памяти. Урок ревью 04.09: клиент,
проверенный против собственной заглушки, разошёлся с живыми ручками в
двух местах (ключ sha, путь файла) — заглушки контракта запрещены.

Охраняемое: пакет уходит целиком и опознаётся по sha; движок отбора
едет только с пейрингом; ожидание завершается по done задачи, «пакета
нет»/чужой пакет — отказ сразу; артефакты читаются алиасами COSMOS;
сертифицированность — по выдаче нажатия, не по старому файлу
сертификата; закрытый гейт запуска — задача не нажимается; превышение
времени пейринга — стоп серии без нажатия ростеринга."""
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tools import certify_button as cb
from tools.cosmos_press_client import CosmosPress, CosmosPressError, launch_gate
from _stand_identity import ADMIN_EMAIL

ADMIN = ADMIN_EMAIL


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from webapp import users
    cfg = tmp_path_factory.mktemp("press-client-auth")
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
    try:
        with TestClient(app_module.app) as c:
            yield c
    finally:
        mp.undo()


class Stand:
    """Состояние площадки в памяти: приём пакета, нажатия, артефакты."""

    def __init__(self, root: Path, polls_to_done=2, certified=True,
                 mg=None, stale_certificate=False):
        self.root = root
        self.package = None
        self.presses = []
        self.polls = 0
        self.polls_to_done = polls_to_done
        self.certified = certified
        self.mg = mg
        self.stale_certificate = stale_certificate
        self.state = None
        self.foreign_sha = None

    # webapp.task_channel.receive(raw, sha, sender, token) -> (status, brief)
    def receive(self, raw, sha, *_):
        self.package = {"sha": sha, "bytes": len(raw)}
        brief = {"verdict": "OK", "sha": sha, "spec_version": "1.14",
                 "source_run": "run-0001", "mismatch": None, "detail": None}
        if self.mg is not None:
            brief["mg"] = self.mg
        return 200, brief

    def press(self, task, **kw):
        if self.package is None:
            from webapp import package_runs as pr
            raise pr.PackageRunError("текущего пакета постановки нет")
        self.presses.append({"task": task, **kw})
        # A10 (ступень M): у каждого нажатия — идентификатор попытки,
        # артефакты снимаются в attempts/<id>/ (W-22: стенд повторяет
        # площадку, чтобы клиент ждал и читал ровно свою попытку).
        self.state = {"task": task, "engine": kw.get("engine") if task == "pairing" else None,
                      "attempt_id": "attempt-%d" % len(self.presses),
                      "state": "кнопка: идёт", "done": False, "error": None,
                      "verdict": None}
        self.polls = 0
        # Шаг 3 (05.09.2026): третье нажатие — ростеринг на пейринге —
        # кладёт артефакты в свой каталог (button-on-pairing).
        self._write(task, on_pairing=bool(kw.get("pairing_run_id")))
        return self.status()

    def status(self):
        if self.package is None:
            return {"present": False}
        st = dict(self.state or {"task": None, "engine": None, "state": None,
                                 "done": None, "error": None, "verdict": None})
        if self.state:
            self.polls += 1
            if self.polls >= self.polls_to_done:
                st["done"] = True
                st["verdict"] = ("пейринг построен" if st["task"] == "pairing"
                                 else "допустимо")
                st["state"] = "кнопка: %s" % st["verdict"]
        return {"present": True, "package_sha": self.foreign_sha or self.package["sha"],
                **st, "tasks_done": [p["task"] for p in self.presses],
                "llm_scope": "any", "files": {}, "engines": ["cpsat", "cplex", "highs"]}

    def _write(self, task, on_pairing=False):
        from webapp import package_runs as pr
        sub = ("button-pairing" if task == "pairing"
               else ("button-on-pairing" if on_pairing else "button"))
        d = self.root / sub
        d.mkdir(parents=True, exist_ok=True)
        delivery = {"verdict": "пейринг построен" if task == "pairing" else "допустимо",
                    "chains": 9 if task == "pairing" else None,
                    "schedule": {"score": "0hard/-6soft", "solver_run_id": "r-1"},
                    "certified": self.certified}
        (d / "delivery.json").write_text(json.dumps(delivery, ensure_ascii=False),
                                         encoding="utf-8")
        cert = {"certificate": {"certified": True, "findings": 0,
                                "counted_share": {"of": 11, "counted": 11}},
                "checks": [{"status": "прогнана"}] * 11, "findings": []}
        if self.certified or self.stale_certificate:
            (d / "certificate.json").write_text(json.dumps(cert, ensure_ascii=False),
                                                encoding="utf-8")
        # Снимок артефактов попытки — как _snapshot площадки.
        import shutil
        snap = self.root / "attempts" / self.state["attempt_id"] / sub
        shutil.rmtree(snap, ignore_errors=True)
        shutil.copytree(d, snap)
        assert pr.ALLOWED_FILES  # словарь алиасов площадки — живой

    def artifact_path(self, alias, attempt=None):
        from webapp import package_runs as pr
        if alias not in pr.ALLOWED_FILES:
            raise pr.PackageRunError("неизвестный артефакт %r" % alias)
        sub, name = pr.ALLOWED_FILES[alias]
        root = self.root / "attempts" / attempt if attempt else self.root
        p = root / sub / name
        if not p.is_file():
            raise pr.PackageRunError("артефакт %s ещё не построен" % alias)
        return p, name


@pytest.fixture
def stand(client, tmp_path, monkeypatch):
    """Площадка в памяти поверх настоящих ручек webapp.app."""
    def make(**kw):
        s = Stand(tmp_path / "package-runs", **kw)
        from webapp import package_runs as pr, task_channel as tc
        monkeypatch.setattr(tc, "receive", s.receive)
        monkeypatch.setattr(pr, "press", s.press)
        monkeypatch.setattr(pr, "status", s.status)
        monkeypatch.setattr(pr, "artifact_path", s.artifact_path)
        import tools.cosmos_press_client as cpc
        monkeypatch.setattr(cpc, "SLEEP", lambda *_: None)
        return s
    return make


def _zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps({"spec_version": "1.14"}))
    return buf.getvalue()


def test_клиент_по_живым_ручкам(client, stand):
    S = stand(polls_to_done=3)
    p = CosmosPress(client)
    body = _zip()
    up = p.upload(body, name="run-0001-task-package.zip")
    assert up["sha"] == hashlib.sha256(body).hexdigest()   # ключ площадки — sha
    assert S.package["bytes"] == len(body)
    st = p.press("pairing", engine="cplex", spent=45, unimproved_spent=5, poll_s=3)
    assert st["present"] and st["state"] == "кнопка: идёт"
    assert S.presses[-1]["engine"] == "cplex"
    assert S.presses[-1]["spent_limit_s"] == 45
    assert S.presses[-1]["unimproved_spent_limit_s"] == 5
    states = []
    st = p.wait("pairing", timeout_s=60, poll_s=1, on_state=states.append,
                package_sha=up["sha"])
    assert st["done"] is True and st["task"] == "pairing" and st["engine"] == "cplex"
    assert states == ["кнопка: идёт", "кнопка: пейринг построен"]
    d = p.file_json("выдача-кнопки-пейринга")            # путь без сегмента задачи
    assert d["chains"] == 9 and d["certified"] is True
    with pytest.raises(CosmosPressError, match="HTTP 404"):
        p.file_json("нет-такого")


def test_клиент_отказывает_словами(client, stand):
    stand()
    p = CosmosPress(client)
    with pytest.raises(CosmosPressError, match="HTTP 409"):
        p.press("pairing")
    with pytest.raises(CosmosPressError, match="кнопка знает"):
        p.press("trading")
    with pytest.raises(CosmosPressError, match="только к пейрингу"):
        p.press("rostering", engine="cplex")
    with pytest.raises(CosmosPressError, match="известны"):
        p.press("pairing", engine="gurobi")
    # «Пакета нет» — сразу, а не через час.
    with pytest.raises(CosmosPressError, match="пакета на площадке нет"):
        p.wait("pairing", timeout_s=10, poll_s=1)


def test_ожидание_по_времени_и_чужой_пакет(client, stand):
    S = stand(polls_to_done=10 ** 6)
    ticks = iter(range(0, 10 ** 6, 100))
    p = CosmosPress(client, clock=lambda: next(ticks))
    up = p.upload(_zip())
    p.press("rostering", spent=10)
    with pytest.raises(CosmosPressError, match="превышено 250"):
        p.wait("rostering", timeout_s=250, poll_s=1)
    S.foreign_sha = "f" * 64
    with pytest.raises(CosmosPressError, match="не наш"):
        p.wait("rostering", timeout_s=250, poll_s=1, package_sha=up["sha"])


def test_гейт_запуска_из_вердикта_приёма():
    assert launch_gate({"verdict": "OK"}, "pairing") is None
    assert "закрыт" in launch_gate({"mg": {"rostering": {"launch_allowed": False,
                                                          "reason": "нет данных"}}},
                                   "rostering")
    assert launch_gate({"mg": {"rostering": {"launch_allowed": False}}}, "pairing") is None
    assert "расхождение" in launch_gate({"mismatch": {"pairing": "stop≠optimization"}},
                                        "pairing")


def test_инструмент_пишет_честную_карточку(client, stand, tmp_path):
    S = stand(polls_to_done=2)
    out = tmp_path / "out"
    out.mkdir()
    pkg = _zip()   # байты фиксируются один раз: у zip есть время записи
    (out / "run-0001-task-package.zip").write_bytes(pkg)
    card = tmp_path / "card.json"
    rc = cb.main([str(out), "--engine", "highs", "--spent", "30", "--out", str(card)],
                 client=client)
    assert rc == 0
    c = json.loads(card.read_text(encoding="utf-8"))
    assert c["passed"] is True and c["sha256"] == hashlib.sha256(pkg).hexdigest()
    assert [x["task"] for x in S.presses] == ["pairing", "rostering"]
    assert S.presses[0]["engine"] == "highs" and S.presses[1]["engine"] is None
    assert c["pairing"]["chains"] == 9 and c["pairing"]["select_engine"] == "highs"
    assert c["pairing"]["llm_scope"] == "any" and c["pairing"]["package_sha"] == c["sha256"]
    assert c["rostering"]["certified"] is True and c["rostering"]["checks_run"] == 11


def test_старый_сертификат_не_засчитывается(client, stand, tmp_path):
    """Остановка кнопки перезаписывает выдачу без certified, а файл
    сертификата прежнего нажатия остаётся — карточка обязана сказать
    «не сертифицировано» (живой ложный зелёный 03.09)."""
    stand(polls_to_done=1, certified=False, stale_certificate=True)
    out = tmp_path / "out"
    out.mkdir()
    (out / "run-0001-task-package.zip").write_bytes(_zip())
    card = tmp_path / "card.json"
    rc = cb.main([str(out), "--out", str(card)], client=client)
    assert rc == 1
    c = json.loads(card.read_text(encoding="utf-8"))
    assert c["passed"] is False and c["pairing"]["certified"] is False
    assert "не засчитан" in c["pairing"]["certificate_note"]


def test_закрытый_гейт_не_нажимается(client, stand, tmp_path):
    S = stand(polls_to_done=1, mg={"pairing": {"launch_allowed": True},
                                   "rostering": {"launch_allowed": False,
                                                 "reason": "критерий не задан"}})
    out = tmp_path / "out"
    out.mkdir()
    (out / "run-0001-task-package.zip").write_bytes(_zip())
    card = tmp_path / "card.json"
    rc = cb.main([str(out), "--out", str(card)], client=client)
    assert rc == 1
    c = json.loads(card.read_text(encoding="utf-8"))
    assert [x["task"] for x in S.presses] == ["pairing"]
    assert c["rostering"]["skipped"] is True and "критерий" in c["rostering"]["error"]


def test_превышение_времени_останавливает_серию(client, stand, tmp_path, monkeypatch):
    S = stand(polls_to_done=10 ** 6)
    ticks = iter(range(0, 10 ** 7, 500))
    import tools.cosmos_press_client as cpc
    monkeypatch.setattr(cpc, "CLOCK", lambda: next(ticks))
    out = tmp_path / "out"
    out.mkdir()
    (out / "run-0001-task-package.zip").write_bytes(_zip())
    card = tmp_path / "card.json"
    rc = cb.main([str(out), "--timeout", "1000", "--out", str(card)], client=client)
    assert rc == 1
    c = json.loads(card.read_text(encoding="utf-8"))
    assert "превышено" in c["pairing"]["error"] and "stopped" in c
    assert [x["task"] for x in S.presses] == ["pairing"]   # ростеринг не нажат
    assert "rostering" not in c


def test_инструмент_без_пакета_и_без_адреса(tmp_path):
    with pytest.raises(SystemExit, match="cosmos-url"):
        cb.main([str(tmp_path)])
    with pytest.raises(SystemExit, match="нет пакета"):
        cb.main([str(tmp_path), "--cosmos-url", "http://x"])


def test_третье_нажатие_на_утверждённом_пейринге_явно(client, stand, tmp_path):
    """Шаг 3 плана 05.09.2026: --on-pairing после двух нажатий делает
    третье — ростеринг на пейринге — с pairingRunId из выдачи нажатия
    pairing этой серии, названным явно; карточка несёт его отдельной
    записью, артефакты читаются по алиасам «…-на-пейринге»."""
    S = stand(polls_to_done=2)
    out = tmp_path / "out"
    out.mkdir()
    (out / "run-0001-task-package.zip").write_bytes(_zip())
    card = tmp_path / "card.json"
    rc = cb.main([str(out), "--spent", "30", "--on-pairing", "--out", str(card)],
                 client=client)
    assert rc == 0
    assert [x["task"] for x in S.presses] == ["pairing", "rostering", "rostering"]
    assert S.presses[1].get("pairing_run_id") is None
    assert S.presses[2]["pairing_run_id"] == "r-1"
    c = json.loads(card.read_text(encoding="utf-8"))
    assert c["rostering_on_pairing"]["pairing_run_id"] == "r-1"
    assert c["rostering_on_pairing"]["certified"] is True
    assert c["rostering_on_pairing"]["solver_run_id"] == "r-1"
    # явный прогон аргументом — сильнее выдачи
    S2 = stand(polls_to_done=2)
    rc2 = cb.main([str(out), "--spent", "30", "--pairing-run-id", "r-77"], client=client)
    assert rc2 == 0
    assert S2.presses[2]["pairing_run_id"] == "r-77"


# ── Единая версия 06.09.2026 (W-16/W-17/W-19/W-24) ──

def test_separate_rostering_requires_explicit_selection(tmp_path):
    """--task rostering без --pairing-run-id — отказ до любой работы:
    последний доступный прогон пейринга не подставляется."""
    with pytest.raises(SystemExit, match="pairing-run-id"):
        cb.main([str(tmp_path), "--task", "rostering"])
    with pytest.raises(SystemExit, match="pairing-run-id"):
        cb.main([str(tmp_path), "--task", "rostering", "--pairing-run-id", "  "])
    with pytest.raises(SystemExit, match="task pairing"):
        cb.main([str(tmp_path), "--task", "pairing", "--pairing-run-id", "r-1"])


def test_cli_explicit_existing_run_is_forwarded(client, stand, tmp_path):
    """--task rostering: ОДНО нажатие — ростеринг на явно названном
    прогоне (не последнем), без пейринга и холодного шага; карточка несёт
    запрошенный прогон, passed судится только по этой задаче."""
    s = stand(polls_to_done=1)
    pkg = tmp_path / "run-0001-task-package.zip"
    pkg.write_bytes(_zip())
    card = tmp_path / "card.json"
    assert cb.main([str(tmp_path), "--task", "rostering",
                    "--pairing-run-id", "chosen-not-latest", "--out", str(card)],
                   client=client) == 0
    assert len(s.presses) == 1 and s.presses[0]["task"] == "rostering"
    assert s.presses[0]["pairing_run_id"] == "chosen-not-latest"
    c = json.loads(card.read_text(encoding="utf-8"))
    assert c["task_requested"] == "rostering"
    assert c["pairing_run_id_requested"] == "chosen-not-latest"
    assert c["rostering_on_pairing"]["pairing_run_id_requested"] == "chosen-not-latest"
    assert c["rostering_on_pairing"]["attempt_id"] == "attempt-1"
    assert c["judged_tasks"] == ["rostering_on_pairing"] and c["passed"] is True
    assert "pairing" not in c and "rostering" not in c
    # --task pairing — только пейринг, passed по нему одному.
    s2 = stand(polls_to_done=1)
    assert cb.main([str(tmp_path), "--task", "pairing", "--out", str(card)],
                   client=client) == 0
    assert [x["task"] for x in s2.presses] == ["pairing"]
    c2 = json.loads(card.read_text(encoding="utf-8"))
    assert c2["judged_tasks"] == ["pairing"] and "rostering" not in c2


def test_client_wait_rejects_replaced_attempt(client, stand):
    """Ожидание привязано к попытке СВОЕГО нажатия: текущая попытка
    площадки другая — отказ словами, чужой done за свой не принимается."""
    stand(polls_to_done=1)
    p = CosmosPress(client)
    p.upload(_zip())
    st = p.press("pairing")
    assert st["attempt_id"] == "attempt-1"
    with pytest.raises(CosmosPressError, match="попытка заменена"):
        p.wait("pairing", attempt_id="not-this-attempt")
    # Своя попытка — ждётся и читается из своего снимка.
    assert p.wait("pairing", attempt_id="attempt-1")["done"] is True
    assert p.file_json("выдача-кнопки-пейринга", attempt="attempt-1")["chains"] == 9
    with pytest.raises(CosmosPressError, match="HTTP 404"):
        p.file_json("выдача-кнопки-пейринга", attempt="attempt-9")


def test_инструмент_ждёт_и_читает_свою_попытку(client, stand, tmp_path):
    """W-17: серия передаёт attempt_id из ответа нажатия в ожидание и в
    чтение артефактов каждой задачи; карточка несёт попытки и поля
    выбранного пейринга (W-18: None у штатных задач — не ошибка)."""
    S = stand(polls_to_done=2)
    out = tmp_path / "out"
    out.mkdir()
    (out / "run-0001-task-package.zip").write_bytes(_zip())
    card = tmp_path / "card.json"
    rc = cb.main([str(out), "--on-pairing", "--out", str(card)], client=client)
    assert rc == 0
    c = json.loads(card.read_text(encoding="utf-8"))
    assert [x["task"] for x in S.presses] == ["pairing", "rostering", "rostering"]
    assert (c["pairing"]["attempt_id"], c["rostering"]["attempt_id"],
            c["rostering_on_pairing"]["attempt_id"]) == ("attempt-1", "attempt-2",
                                                         "attempt-3")
    assert c["rostering_on_pairing"]["pairing_run_id_requested"] == "r-1"
    assert c["judged_tasks"] == ["pairing", "rostering", "rostering_on_pairing"]
    for key in ("pairing_selection", "catalog_sha256", "next_actions", "selection_note"):
        assert key in c["pairing"] and c["pairing"][key] is None
