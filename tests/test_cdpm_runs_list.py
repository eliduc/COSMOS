# -*- coding: utf-8 -*-
"""Список прогонов панели CDPM (просьба владельца 06.09.2026): дата
создания, ручной порядок (вверх/вниз), архив выбранных прогонов и
счётчик номеров без повторного использования.

Каталог прогонов уводится в tmp через CDPM_RUNS_DIR (та же дисциплина,
что в test_cdpm_webui); прогоны рождаются через HTTP-обвязку, а
«старые» прогоны без created_at и занятые прогоны раскладываются на
диске минимальным status.json."""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cdpm import webui
from test_cdpm_extract import _RULES_A, _RULES_B, fake_llm


@pytest.fixture(autouse=True)
def _s06r2_declared_layout(declared_test_layout):
    """S06-R2 (06.09.2026): панель требует объявленной раскладки ролей —
    тесты этого файла объявляют явную тестовую (conftest)."""


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("CDPM_SETTINGS_FILE",
                       str(tmp_path / "cdpm-settings.json"))
    monkeypatch.setattr(webui, "llm_factory", lambda scope: fake_llm)
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: fake_llm)
    return TestClient(webui.app)


def _upload(client):
    return client.post("/api/runs", files=[
        ("files", ("RULES-A.md", _RULES_A.encode("utf-8"),
                   "text/markdown")),
        ("files", ("RULES-B.md", _RULES_B.encode("utf-8"),
                   "text/markdown")),
    ], params={"data_class": "synthetic"})


def _create(client) -> str:
    r = _upload(client)
    assert r.status_code == 200, r.text
    return r.json()["run"]


def _plant_run(root: Path, run_id: str, status: dict | None = None) -> Path:
    """Прогон, разложенный на диске руками (старая установка: без
    created_at, либо занятый стадией)."""
    run_dir = root / run_id
    (run_dir / "src").mkdir(parents=True)
    if status is not None:
        (run_dir / "status.json").write_text(
            json.dumps(status, ensure_ascii=False), encoding="utf-8")
    return run_dir


_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}")


# ── (a) дата создания ────────────────────────────────────────────────

def test_created_at_written_and_listed(client):
    run_id = _create(client)
    st = client.get(f"/api/runs/{run_id}").json()
    assert _ISO_RE.fullmatch(st["created_at"]), st["created_at"]
    listed = client.get("/api/runs").json()
    assert [r["run"] for r in listed] == [run_id]
    assert listed[0]["created_at"] == st["created_at"]
    assert listed[0]["position"] == 0


def test_created_at_fallback_from_directory(client, tmp_path):
    import os
    root = webui.runs_root()
    run_dir = _plant_run(root, "run-0001", {"state": "заморожено",
                                            "files": []})
    # Старый прогон: дата в списке обязана прийти из каталога, а не
    # «сейчас». Где система хранит время рождения каталога (Windows),
    # берётся оно; иначе — самое раннее из ctime/mtime, которое здесь
    # отодвинуто в 2023 год.
    import time
    from datetime import datetime
    old = 1_700_000_000  # 2023-11-14
    os.utime(run_dir, (old, old))
    st = os.stat(run_dir)
    birth = getattr(st, "st_birthtime", None)
    expected = datetime.fromtimestamp(birth if birth else old) \
        .astimezone().isoformat(timespec="seconds")
    time.sleep(1.2)   # «сейчас» гарантированно отличается от рождения
    got = client.get("/api/runs").json()
    assert [r["run"] for r in got] == ["run-0001"]
    assert got[0]["state"] == "заморожено"
    assert got[0]["created_at"] and _ISO_RE.fullmatch(got[0]["created_at"])
    assert got[0]["created_at"] == expected
    if not birth:
        assert got[0]["created_at"].startswith("2023-11-14")


def test_unreadable_status_does_not_break_listing(client):
    root = webui.runs_root()
    _create(client)
    bad = _plant_run(root, "run-0002")
    (bad / "status.json").write_text("{рваный json", encoding="utf-8")
    got = client.get("/api/runs").json()
    assert [r["run"] for r in got] == ["run-0001", "run-0002"]
    assert got[1]["state"] is None
    assert got[1]["created_at"]


# ── (b) ручной порядок ───────────────────────────────────────────────

def test_manual_order_put_get_and_listing_positions(client):
    a, b, c = _create(client), _create(client), _create(client)
    assert (a, b, c) == ("run-0001", "run-0002", "run-0003")
    # Без order.json — по имени.
    assert client.get("/api/runs/order").json() == {"order": [a, b, c]}
    assert [r["position"] for r in client.get("/api/runs").json()] \
        == [0, 1, 2]
    # Перестановка: c наверх; не названный b встаёт в конец по имени.
    r = client.put("/api/runs/order", json={"order": [c, a]})
    assert r.status_code == 200, r.text
    assert r.json() == {"order": [c, a, b]}
    assert client.get("/api/runs/order").json() == {"order": [c, a, b]}
    listed = client.get("/api/runs").json()
    assert [(x["run"], x["position"]) for x in listed] \
        == [(c, 0), (a, 1), (b, 2)]
    stored = json.loads((webui.runs_root() / "order.json")
                        .read_text(encoding="utf-8"))
    assert stored == {"order": [c, a]}
    assert not list(webui.runs_root().glob("*.tmp"))


def test_order_put_rejects_unknown_and_malformed_ids(client):
    a = _create(client)
    r = client.put("/api/runs/order", json={"order": [a, "run-0099"]})
    assert r.status_code == 400
    assert "не существует" in r.json()["detail"]
    assert not (webui.runs_root() / "order.json").exists()
    r = client.put("/api/runs/order", json={"order": ["../x"]})
    assert r.status_code == 400
    assert "недопустимый" in r.json()["detail"]
    r = client.put("/api/runs/order", json={"order": "run-0001"})
    assert r.status_code == 400


def test_order_ignores_vanished_runs(client):
    a, b = _create(client), _create(client)
    (webui.runs_root() / "order.json").write_text(
        json.dumps({"order": [b, "run-0077", a]}), encoding="utf-8")
    assert client.get("/api/runs/order").json() == {"order": [b, a]}
    assert [r["run"] for r in client.get("/api/runs").json()] == [b, a]


# ── (c) архив ────────────────────────────────────────────────────────

def test_archive_moves_runs_and_cleans_order_and_counter(client):
    root = webui.runs_root()
    a, b, c = _create(client), _create(client), _create(client)
    assert client.put("/api/runs/order",
                      json={"order": [c, b, a]}).status_code == 200
    r = client.post("/api/runs/archive", json={"runs": [a, c]})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["archived"] == [a, c]
    assert got["left"] == 1
    archive = Path(got["archive_dir"])
    assert archive == (root.parent / "cdpm-runs-archive").resolve()
    assert (archive / a / "src" / "RULES-A.md").is_file()
    assert (archive / c / "status.json").is_file()
    assert not (root / a).exists() and not (root / c).exists()
    assert (root / b).is_dir()
    assert [x["run"] for x in client.get("/api/runs").json()] == [b]
    assert json.loads((root / "order.json").read_text(encoding="utf-8")) \
        == {"order": [b]}
    assert json.loads((root / "counter.json").read_text(encoding="utf-8")) \
        == {"last": 3}
    # Прогон в архиве — не существует для ручек.
    assert client.get(f"/api/runs/{a}").status_code == 404


def test_archive_refuses_busy_run_and_moves_nothing(client):
    root = webui.runs_root()
    a = _create(client)
    _plant_run(root, "run-0002", {"state": "извлечение идёт"})
    _plant_run(root, "run-0003", {"state": "заморожено",
                                  "data_state": "на паузе: проверки"})
    order_before = {"order": ["run-0003", a, "run-0002"]}
    (root / "order.json").write_text(json.dumps(order_before),
                                     encoding="utf-8")
    (root / "counter.json").write_text(json.dumps({"last": 3}),
                                       encoding="utf-8")
    r = client.post("/api/runs/archive",
                    json={"runs": [a, "run-0002", "run-0003"]})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "run-0002" in detail and "извлечение идёт" in detail
    assert "run-0003" in detail
    assert "ничего не перенесено" in detail
    for rid in (a, "run-0002", "run-0003"):
        assert (root / rid).is_dir()
    assert not (root.parent / "cdpm-runs-archive" / a).exists()
    # Служебные файлы при отказе не тронуты.
    assert json.loads((root / "order.json").read_text(encoding="utf-8")) \
        == order_before
    assert json.loads((root / "counter.json").read_text(encoding="utf-8")) \
        == {"last": 3}


def test_archive_refuses_unreadable_status_fail_closed(client):
    """Статус не прочитан → занятость неизвестна → 409, ничего не
    перенесено (ревью 06.09: прежде такой прогон уходил без проверки)."""
    root = webui.runs_root()
    a = _create(client)
    bad = _plant_run(root, "run-0002")
    (bad / "status.json").write_text("{рваный json", encoding="utf-8")
    r = client.post("/api/runs/archive", json={"runs": [a, "run-0002"]})
    assert r.status_code == 409, r.text
    assert "run-0002" in r.json()["detail"]
    assert "не прочитан статус" in r.json()["detail"]
    assert (root / a).is_dir() and (root / "run-0002").is_dir()
    assert not (root.parent / "cdpm-runs-archive").exists()


def test_archive_partial_failure_keeps_files_consistent(client,
                                                        monkeypatch):
    """Срыв переноса на втором прогоне: первый уже в архиве — order.json
    и counter.json правятся по фактически ушедшим, ответ 500 называет и
    срыв, и уже перенесённые."""
    root = webui.runs_root()
    a, b = _create(client), _create(client)
    assert client.put("/api/runs/order",
                      json={"order": [b, a]}).status_code == 200
    real_rename = webui.os.rename

    def flaky_rename(src, dst, *args, **kwargs):
        if str(src).endswith(b):
            raise OSError("диск занят")
        return real_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(webui.os, "rename", flaky_rename)
    r = client.post("/api/runs/archive", json={"runs": [a, b]})
    assert r.status_code == 500, r.text
    detail = r.json()["detail"]
    assert b in detail and "уже в архиве: " + a in detail
    assert not (root / a).exists() and (root / b).is_dir()
    assert (root / b / "src" / "RULES-A.md").is_file()   # каталог цел
    assert json.loads((root / "order.json").read_text(encoding="utf-8")) \
        == {"order": [b]}
    # Счётчик не опускается: после создания b он уже был 2.
    assert json.loads((root / "counter.json").read_text(encoding="utf-8")) \
        == {"last": 2}
    assert [x["run"] for x in client.get("/api/runs").json()] == [b]


def test_archive_refuses_run_with_running_smt_thread(client):
    root = webui.runs_root()
    a = _create(client)
    _plant_run(root, "run-0002", {"state": "извлечено",
                                  "smt_state": "SMT-проверка идёт"})
    r = client.post("/api/runs/archive", json={"runs": [a, "run-0002"]})
    assert r.status_code == 409, r.text
    assert "run-0002" in r.json()["detail"]
    assert (root / a).is_dir() and (root / "run-0002").is_dir()


def test_malformed_service_files_are_tolerated_and_ids_deduplicated(
        client):
    root = webui.runs_root()
    a, b = _create(client), _create(client)
    (root / "order.json").write_text("{не json", encoding="utf-8")
    (root / "counter.json").write_text('{"last": "много"}', encoding="utf-8")
    # Рваный order.json — порядок по имени; рваный counter — от нуля.
    assert client.get("/api/runs/order").json() == {"order": [a, b]}
    assert [r["run"] for r in client.get("/api/runs").json()] == [a, b]
    c = _create(client)
    assert c == "run-0003"
    # Повторы номеров в теле — один раз.
    r = client.put("/api/runs/order", json={"order": [c, c, a]})
    assert r.json() == {"order": [c, a, b]}
    r = client.post("/api/runs/archive", json={"runs": [a, a]})
    assert r.status_code == 200 and r.json()["archived"] == [a]


def test_archive_unknown_id_is_404_and_moves_nothing(client):
    root = webui.runs_root()
    a = _create(client)
    r = client.post("/api/runs/archive", json={"runs": [a, "run-0042"]})
    assert r.status_code == 404, r.text
    assert "run-0042" in r.json()["detail"]
    assert (root / a).is_dir()
    assert not (root.parent / "cdpm-runs-archive" / a).exists()
    r = client.post("/api/runs/archive", json={"runs": ["oops"]})
    assert r.status_code == 400
    r = client.post("/api/runs/archive", json={"runs": []})
    assert r.status_code == 400


def test_archive_twice_same_number_keeps_both_copies(client):
    """Занятое имя в архиве получает хвост с меткой времени — первая
    копия не затирается."""
    root = webui.runs_root()
    archive = root.parent / "cdpm-runs-archive"
    a = _create(client)
    assert client.post("/api/runs/archive",
                       json={"runs": [a]}).status_code == 200
    _plant_run(root, a, {"state": "заморожено"})
    assert client.post("/api/runs/archive",
                       json={"runs": [a]}).status_code == 200
    names = sorted(p.name for p in archive.iterdir())
    assert len(names) == 2 and names[0] == a
    assert re.fullmatch(rf"{a}-\d{{8}}-\d{{6}}", names[1]), names


# ── (d) номера не переиспользуются ───────────────────────────────────

def test_number_after_archiving_newest_is_not_reused(client):
    root = webui.runs_root()
    a, b = _create(client), _create(client)
    assert client.post("/api/runs/archive",
                       json={"runs": [b]}).status_code == 200
    assert [x["run"] for x in client.get("/api/runs").json()] == [a]
    c = _create(client)
    assert c == "run-0003"
    assert json.loads((root / "counter.json").read_text(encoding="utf-8")) \
        == {"last": 3}


def test_counter_absent_on_old_installation_keeps_working(client):
    """Установка без counter.json: номер — следующий за максимумом
    существующих, как и прежде; счётчик появляется."""
    root = webui.runs_root()
    _plant_run(root, "run-0007", {"state": "заморожено"})
    assert not (root / "counter.json").exists()
    assert _create(client) == "run-0008"
    assert json.loads((root / "counter.json").read_text(encoding="utf-8")) \
        == {"last": 8}
