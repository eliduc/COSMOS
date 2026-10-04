# -*- coding: utf-8 -*-
"""Внешний аудит 04.09.2026, A10 (очередь 3, ступень M) — попытка нажатия
с идентификатором: своя запись в состоянии, снимок артефактов на
нажатие, статус и файлы по ID, прерванная попытка помечена.

Стенды с известным ответом: (1) нажатие получает ID, после конца —
запись «завершено» с вердиктом и снимком attempts/<id>/button-pairing/;
(2) статус по ID отдаёт запись, перечень файлов и алиасы, чужой ID —
отказ словами; (3) два нажатия — две попытки, снимки обеих живы,
текущий каталог — последней; (4) артефакт по ID читается из снимка;
(5) сирота «идёт» без живой нити помечается по ID как прерванная.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))

from tests.test_run_button import FakeSolver, _CATALOG_OK   # noqa: E402
from tests.test_package_runs import _make_current, _pairing_kwargs  # noqa: E402


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data-root"))
    for k in ("CREW_TASK_INBOX", "CREW_TASK_PACKAGE", "CREW_VMP_REGISTRY",
              "CREW_SOLVER_URL"):
        monkeypatch.delenv(k, raising=False)
    from webapp import flow, task_channel
    monkeypatch.setitem(flow._READY_CACHE, "key", None)
    monkeypatch.setitem(task_channel._CARD_CACHE, "key", None)
    from vmp import registry_check as _regc
    from vmp.run import button as btn
    root = Path(btn.__file__).resolve().parent.parent.parent

    def _fake_press_registry(run_dir, model, task):
        reg = {"created": "тест", "scope": "кнопка: тест",
               "code_fingerprints": _regc.code_fingerprints(root),
               "rules": [{"rule_id": str(r["id"]), "verdict": "covered_full",
                          "title": r.get("title") or ""}
                         for r in (model or {}).get("rules") or []]}
        path = Path(run_dir) / "registry" / "vmp-executors-registry.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
        return path
    monkeypatch.setattr(btn, "press_registry", _fake_press_registry)


def _press(pr, tag: str):
    fakes = _pairing_kwargs()
    fakes.pop("pairing_builder", None)

    def builder(payload, args, out):
        cat = dict(_CATALOG_OK, label=tag)
        raw = json.dumps(cat).encode("utf-8")
        (Path(out) / "builder-catalog.json").write_bytes(raw)
        return raw
    return pr.press("pairing", engine="cpsat", solver=FakeSolver(),
                    pairing_builder=builder, start_thread=False,
                    sleep=lambda *_: None, **fakes)


def test_press_gets_an_attempt_id_record_and_snapshot(tmp_path):
    from webapp import package_runs as pr
    sha = _make_current(tmp_path)
    st = _press(pr, "one")
    assert st["done"] is True and st["verdict"] == "пейринг построен", st
    aid = st["attempt_id"]
    assert aid and st["attempts"][0]["id"] == aid
    rec = st["attempts"][0]
    assert rec["state"] == "завершено" and rec["done"] is True
    assert rec["verdict"] == "пейринг построен" and rec["task"] == "pairing"
    assert rec["catalog"] == "attempts/%s/button-pairing" % aid
    wd = pr.workdir_for(sha)
    assert (wd / "attempts" / aid / "button-pairing" / "delivery.json").is_file()
    # статус по ID
    one = pr.status(attempt=aid)
    assert one["attempt"]["id"] == aid and one["current"] is True
    assert "button-pairing/delivery.json" in one["files"]
    assert "pairing_delivery" in one["aliases"] or one["aliases"]
    with pytest.raises(pr.PackageRunError):
        pr.status(attempt="нет-такой")


def test_two_presses_keep_both_snapshots_and_current_is_the_last(tmp_path):
    from webapp import package_runs as pr
    sha = _make_current(tmp_path)
    a1 = _press(pr, "one")["attempt_id"]
    a2 = _press(pr, "two")["attempt_id"]
    assert a1 != a2
    st = pr.status()
    assert [r["id"] for r in st["attempts"]] == [a2, a1]      # новые первыми
    wd = pr.workdir_for(sha)
    cat1 = json.loads((wd / "attempts" / a1 / "button-pairing" /
                       "builder-catalog.json").read_text(encoding="utf-8"))
    cat2 = json.loads((wd / "attempts" / a2 / "button-pairing" /
                       "builder-catalog.json").read_text(encoding="utf-8"))
    assert cat1["label"] == "one" and cat2["label"] == "two"
    cur = json.loads((wd / "button-pairing" / "builder-catalog.json")
                     .read_text(encoding="utf-8"))
    assert cur["label"] == "two"
    # артефакт по ID — из снимка первой попытки
    alias = next(a for a, (sub, name) in pr.ALLOWED_FILES.items()
                 if sub == "button-pairing" and name == "builder-catalog.json")
    path, name = pr.artifact_path(alias, attempt=a1)
    assert json.loads(path.read_text(encoding="utf-8"))["label"] == "one"
    with pytest.raises(pr.PackageRunError):
        pr.artifact_path(alias, attempt="../..")


def test_orphaned_attempt_is_marked_interrupted_by_id(tmp_path, monkeypatch):
    """Чужой номер процесса объявляется МЁРТВЫМ явно: с 10.09.2026
    сторож спрашивает операционную систему, жив ли процесс, и случайное
    совпадение номера os.getpid() + 100000 с чужой живой программой
    сделало бы стенд плавающим (чужой живой процесс — не сирота, а
    держатель линии). Образец подмены — tests/test_package_runs.py."""
    from webapp import package_runs as pr
    monkeypatch.setattr(pr, "_pid_alive", lambda pid: int(pid) == os.getpid())
    sha = _make_current(tmp_path)
    wd = pr.workdir_for(sha)
    aid = "20260905T000000-abc123"
    pr._write_state(wd, {"task": "pairing", "state": "кнопка: идёт",
                         "done": False, "pid": os.getpid() + 100000,
                         "started_at": "2026-09-05T00:00:00",
                         "attempt_id": aid,
                         "attempts": [{"id": aid, "task": "pairing",
                                       "state": "идёт", "done": False}]})
    st = pr.status()
    assert st["done"] is True and "прервано" in str(st["state"])
    rec = st["attempts"][0]
    assert rec["id"] == aid and rec["state"] == "прервано перезапуском"
    assert rec["done"] is True and rec["error"]
