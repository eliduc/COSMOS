# -*- coding: utf-8 -*-
"""Сторож-диагност построителя цепочек гаснет вместе со сборкой.

Находка CDPM 08.09.2026: поток «ba-watchdog» жил до конца процесса и
писал файл рядом с каталогом каждые полминуты — встроенные вызовы и
тесты, зовущие сборку в своём процессе, оставляли писателя на весь
прогон, и любой сторож записей вне временных папок краснел по порядку
запуска. Теперь build() гасит сторожа в finally."""
from __future__ import annotations

import argparse
import threading
import time

import _pairing_builder as pb


def _alive_watchdogs() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "ba-watchdog"]


def test_сторож_останавливается_по_событию(tmp_path):
    out = tmp_path / "run.json"
    stop = pb._start_watchdog(str(out), interval_s=0.05)
    assert isinstance(stop, threading.Event)
    log = tmp_path / "run-watchdog.log"
    deadline = time.monotonic() + 3.0
    while not log.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert log.exists(), "сторож не написал ни одной записи"
    stop.set()
    deadline = time.monotonic() + 3.0
    while _alive_watchdogs() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not _alive_watchdogs(), "поток сторожа пережил событие остановки"
    size = log.stat().st_size
    time.sleep(0.2)
    assert log.stat().st_size == size, "файл сторожа растёт после остановки"


def test_build_гасит_сторожа_и_при_исключении(tmp_path, monkeypatch):
    calls = []

    def body(payload, args, base_catalog, pin_catalog, inject_catalogs):
        calls.append(len(_alive_watchdogs()))
        return {"pairings": []}, {"ok": True}

    monkeypatch.setattr(pb, "_build_body", body)
    args = argparse.Namespace(out=str(tmp_path / "run.json"), level=0)
    catalog, report = pb.build({}, args)
    assert (catalog, report) == ({"pairings": []}, {"ok": True})
    assert calls == [1], "во время сборки сторож должен быть один и живой"
    deadline = time.monotonic() + 3.0
    while _alive_watchdogs() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not _alive_watchdogs(), "сторож пережил успешную сборку"

    def boom(payload, args, base_catalog, pin_catalog, inject_catalogs):
        raise RuntimeError("сборка упала")

    monkeypatch.setattr(pb, "_build_body", boom)
    try:
        pb.build({}, args)
    except RuntimeError:
        pass
    deadline = time.monotonic() + 3.0
    while _alive_watchdogs() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not _alive_watchdogs(), "сторож пережил упавшую сборку"
