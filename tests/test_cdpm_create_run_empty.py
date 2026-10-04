# -*- coding: utf-8 -*-
"""Форма нового прогона без документов (браузерный проход на Проде 04.09:
две формы ушли на сервер без файлов и оставили прогоны «отказ
заморозки, файлов 0»): сервер отказывает словами ДО рождения прогона —
каталога run-… не появляется."""
from fastapi.testclient import TestClient

from cdpm import webui


def test_создание_без_файлов_отказ_без_каталога(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    monkeypatch.setenv("CDPM_RUNS_DIR", str(root))
    with TestClient(webui.app) as c:
        r = c.post("/api/runs?data_class=synthetic", files=[])
        assert r.status_code in (400, 422), r.text
        if r.status_code == 400:
            assert "документы" in r.json()["detail"]
        assert not list(root.glob("run-*")) if root.exists() else True
